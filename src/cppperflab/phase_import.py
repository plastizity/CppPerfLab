"""Import project-owned phase logs through an explicit keyword/regex mapping."""
import json
import math
from string import Formatter
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from .errors import LabError
from .protocol import fields, require, sha256
from .storage import write_json


_UNITS = {"s": Decimal(1), "ms": Decimal("1e-3"), "us": Decimal("1e-6"), "ns": Decimal("1e-9")}
_STATES = ("Measured", "Estimated", "Modeled")


def _invalid(message, details=None):
    raise LabError("invalid_request", message, details)


def _mapping_file(path):
    try:
        value = json.loads(Path(path).read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise LabError("invalid_request", "Cannot read phase mapping", {"path": str(path), "reason": str(error)}) from error
    return value


def _capture(spec, groups, label, key):
    if spec[key] not in groups:
        _invalid(label + " references an absent named capture", {"group": spec[key]})


def _rule_specs(rule):
    specs = []
    for key in ("inclusive", "self", "calls", "correctness"):
        if key in rule:
            specs.append((key, rule[key]))
    specs.extend((name, spec) for name, spec in rule.get("work", {}).items())
    return specs


def _format_pattern(template, rule_id):
    parts = []
    names = set()
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as error:
        _invalid("Invalid phase format template", {"rule": rule_id, "reason": str(error)})
    for literal, name, format_spec, conversion in parsed:
        parts.append(re.escape(literal))
        if name is not None:
            if not re.fullmatch(r"[A-Za-z_]\w*", name) or name in names or format_spec or conversion:
                _invalid("Format fields must be unique simple names without conversion or format spec", {"rule": rule_id, "field": name})
            names.add(name)
            parts.append("(?P<" + name + ">.*?)")
    if not names:
        _invalid("Phase format template must contain named fields", {"rule": rule_id})
    return re.compile("^" + "".join(parts) + "$")


def load_mapping(path):
    """Load and validate the explicit mapping for a project's phase log."""
    config = _mapping_file(path)
    fields(config, ("schema_version", "scope", "rules", "unmatched_lines"),
           ("schema_version", "scope", "rules", "unmatched_lines"), "phase mapping")
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "Unsupported phase mapping version")
    require(isinstance(config["scope"], str) and bool(config["scope"].strip()), "Phase mapping scope must be nonempty")
    require(config["unmatched_lines"] in ("ignore", "error"), "Invalid unmatched_lines policy")
    require(isinstance(config["rules"], list) and bool(config["rules"]), "Phase mapping rules must be nonempty")
    ids = set()
    rule_fields = ("id", "name", "parent_id", "keyword", "pattern", "format", "aggregation", "required", "children_coverage",
                   "inclusive", "self", "calls", "correctness", "work")
    for index, rule in enumerate(config["rules"]):
        fields(rule, rule_fields, ("id", "name", "parent_id", "aggregation", "required", "children_coverage"),
               "phase rule")
        require(isinstance(rule["id"], str) and bool(rule["id"].strip()) and rule["id"] not in ids, "Phase rule IDs must be unique and nonempty")
        ids.add(rule["id"])
        require(isinstance(rule["name"], str) and bool(rule["name"].strip()), "Phase rule name must be nonempty")
        require(rule["parent_id"] is None or isinstance(rule["parent_id"], str), "Invalid phase parent_id")
        require(not ("format" in rule and "pattern" in rule), "A phase rule cannot define both format and pattern")
        if "format" in rule:
            require(isinstance(rule["format"], str) and bool(rule["format"]), "Phase rule format must be nonempty")
            expression = _format_pattern(rule["format"], rule["id"])
            if "keyword" in rule:
                require(isinstance(rule["keyword"], str) and bool(rule["keyword"]), "Phase rule keyword must be nonempty")
            else:
                require(config["unmatched_lines"] == "error", "Format rule without keyword requires unmatched_lines=error")
        elif "pattern" in rule:
            require(isinstance(rule.get("keyword"), str) and bool(rule["keyword"]), "Pattern rule requires a nonempty keyword")
            require(isinstance(rule["pattern"], str) and bool(rule["pattern"]), "Phase rule pattern must be nonempty")
            try:
                expression = re.compile(rule["pattern"])
            except re.error as error:
                _invalid("Invalid phase rule pattern", {"rule": rule["id"], "reason": str(error)})
        else:
            require(isinstance(rule.get("keyword"), str) and bool(rule["keyword"]), "Simple keyword rule requires keyword")
            expression = None
        require(rule["aggregation"] in ("single", "sum"), "Invalid phase aggregation")
        require(type(rule["required"]) is bool, "Phase rule required must be boolean")
        require(rule["children_coverage"] in ("unknown", "complete_nonoverlapping"), "Invalid children_coverage")
        groups = expression.groupindex if expression else None
        for label, spec in _rule_specs(rule):
            source_key = "group" if groups is not None else "keyword"
            if label == "inclusive":
                required_fields = (source_key, "unit", "kind")
            elif label == "self":
                required_fields = (source_key, "unit")
            elif label == "correctness":
                required_fields = (source_key, "values")
            elif label == "calls":
                required_fields = (source_key,)
            else:
                required_fields = (source_key, "unit", "evidence_state")
            fields(spec, required_fields, required_fields, label + " mapping")
            require(isinstance(spec[source_key], str) and bool(spec[source_key]), label + " requires a nonempty " + source_key)
            if groups is not None:
                _capture(spec, groups, label, "group")
        if "inclusive" in rule:
            spec = rule["inclusive"]
            require(spec["unit"] in _UNITS and spec["kind"] in ("total", "per_call_mean"), "Invalid inclusive unit or kind")
        if "self" in rule:
            spec = rule["self"]
            require(spec["unit"] in _UNITS, "Invalid self time unit")
        if "inclusive" in rule and rule["inclusive"]["kind"] == "per_call_mean":
            require("calls" in rule, "per_call_mean requires explicit calls mapping")
        if "correctness" in rule:
            spec = rule["correctness"]
            require(isinstance(spec["values"], dict) and bool(spec["values"]), "correctness values must be nonempty")
            require(all(isinstance(k, str) and v in ("passed", "failed", "not_checked") for k, v in spec["values"].items()),
                    "Invalid correctness source mapping")
        if "work" in rule:
            require(isinstance(rule["work"], dict), "work mapping must be an object")
            for counter, spec in rule["work"].items():
                require(isinstance(counter, str) and bool(counter), "Work counter name must be nonempty")
                require(isinstance(spec["unit"], str) and bool(spec["unit"]) and spec["evidence_state"] in _STATES,
                        "Invalid work unit or evidence state")
    by_id = {r["id"]: r for r in config["rules"]}
    for rule in config["rules"]:
        require(rule["parent_id"] is None or rule["parent_id"] in by_id, "Unknown phase parent_id")
        seen = set()
        current = rule
        while current["parent_id"] is not None:
            require(current["id"] not in seen, "Cyclic phase rule ancestry")
            seen.add(current["id"])
            current = by_id[current["parent_id"]]
    return config


def _number(text, label):
    try:
        value = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise LabError("parse_failed", "Invalid numeric value in selected phase row", {"field": label, "value": text}) from error
    if not value.is_finite() or value < 0:
        raise LabError("parse_failed", "Selected phase value must be finite and nonnegative", {"field": label, "value": text})
    return value


def _as_float(value, label):
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise LabError("parse_failed", label + " exceeds finite JSON numeric range") from error
    if not math.isfinite(result):
        raise LabError("parse_failed", label + " exceeds finite JSON numeric range")
    return result


_NUMERIC_TOKEN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _simple_value(line, key, label, line_number, numeric):
    token = _NUMERIC_TOKEN if numeric else r"[^\s/,;|]+"
    expression = re.compile(r"(?<!\S)" + re.escape(key) + r"\s*[:=]?\s*(" + token + r")(?![\w.+-])")
    matches = list(expression.finditer(line))
    if len(matches) != 1:
        raise LabError("parse_failed", "Keyword field must occur exactly once", {"line": line_number, "field": label,
                       "keyword": key, "matches": len(matches)})
    return matches[0].group(1)


def _simple_values(rule, line, line_number):
    values = {}
    for label, spec in _rule_specs(rule):
        values[spec["keyword"]] = _simple_value(line, spec["keyword"], label, line_number, label != "correctness")
    return values


def convert_log(source, mapping_path, output, expected_sha256=None):
    """Convert a project-specific text log to canonical summary phase evidence."""
    mapping_digest = sha256(mapping_path)
    config = load_mapping(mapping_path)
    if sha256(mapping_path) != mapping_digest:
        raise LabError("storage_failed", "Phase mapping changed while it was being loaded")
    expressions = []
    for rule in config["rules"]:
        expressions.append(_format_pattern(rule["format"], rule["id"]) if "format" in rule
                           else re.compile(rule["pattern"]) if "pattern" in rule else None)
    digest = sha256(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise LabError("storage_failed", "Phase log hash differs from expected_sha256",
                       {"expected": expected_sha256, "actual": digest})
    state = [{"rows": 0, "first": None, "last": None, "inclusive": Decimal(0), "self": Decimal(0),
              "calls": 0, "work": {}, "correctness": [], "matched": False} for _ in config["rules"]]
    input_lines = matched_lines = ignored_lines = 0
    try:
        stream = Path(source).open("r", encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LabError("parse_failed", "Cannot open phase log", {"path": str(source), "reason": str(error)}) from error
    with stream:
        for line_number, line in enumerate(stream, 1):
            input_lines += 1
            candidates = [i for i, rule in enumerate(config["rules"])
                          if ("keyword" in rule and rule["keyword"] in line)
                          or ("format" in rule and "keyword" not in rule)]
            if not candidates:
                if config["unmatched_lines"] == "error":
                    raise LabError("parse_failed", "Unmatched phase log line", {"line": line_number})
                ignored_lines += 1
                continue
            matches = []
            for i in candidates:
                rule = config["rules"][i]
                if "format" in rule:
                    match = expressions[i].fullmatch(line.rstrip("\r\n"))
                elif "pattern" in rule:
                    match = expressions[i].search(line)
                else:
                    match = _simple_values(rule, line, line_number)
                if match is None:
                    raise LabError("parse_failed", "Keyword-selected phase rule did not match its format",
                                   {"line": line_number, "rule": rule["id"]})
                matches.append((i, match))
            if len(matches) != 1:
                raise LabError("parse_failed", "Phase line did not resolve to exactly one rule",
                               {"line": line_number, "candidate_rule_ids": [config["rules"][i]["id"] for i, _ in matches]})
            i, match = matches[0]
            rule = config["rules"][i]
            row = state[i]
            if rule["aggregation"] == "single" and row["rows"]:
                raise LabError("parse_failed", "Single phase rule matched more than once", {"line": line_number, "rule": rule["id"]})

            def get(spec, label):
                value = match.group(spec["group"]) if hasattr(match, "group") else match[spec["keyword"]]
                if value is None:
                    raise LabError("parse_failed", "Selected phase capture is missing", {"line": line_number, "field": label})
                return value

            calls = None
            if "calls" in rule:
                raw = get(rule["calls"], "calls")
                number = _number(raw, "calls")
                if number != number.to_integral_value():
                    raise LabError("parse_failed", "Calls must be an integer", {"line": line_number, "value": raw})
                calls = int(number)
                row["calls"] += calls
            if "inclusive" in rule:
                spec = rule["inclusive"]
                val = _number(get(spec, "inclusive"), "inclusive") * _UNITS[spec["unit"]]
                if spec["kind"] == "per_call_mean":
                    val *= calls
                row["inclusive"] += val
            if "self" in rule:
                spec = rule["self"]
                row["self"] += _number(get(spec, "self"), "self") * _UNITS[spec["unit"]]
            for name, spec in rule.get("work", {}).items():
                value = _number(get(spec, name), name)
                row["work"].setdefault(name, Decimal(0))
                row["work"][name] += value
            if "correctness" in rule:
                spec = rule["correctness"]
                raw = get(spec, "correctness")
                if raw not in spec["values"]:
                    raise LabError("parse_failed", "Unknown correctness source value", {"line": line_number, "value": raw})
                row["correctness"].append(spec["values"][raw])
            row["rows"] += 1
            row["matched"] = True
            row["first"] = row["first"] or line_number
            row["last"] = line_number
            matched_lines += 1
    if sha256(source) != digest:
        raise LabError("storage_failed", "Phase log changed while it was being parsed")
    absent = [config["rules"][i]["id"] for i, row in enumerate(state) if not row["matched"] and config["rules"][i]["required"]]
    if absent:
        raise LabError("parse_failed", "Required phase rule did not match", {"rule_ids": absent})
    stages = []
    for rule, row in zip(config["rules"], state):
        inclusive = None
        if "inclusive" in rule and row["matched"]:
            inclusive = {"seconds": _as_float(row["inclusive"], "inclusive seconds"),
                         "evidence_state": "Estimated" if rule["inclusive"]["kind"] == "per_call_mean" else "Measured"}
        own = None
        if "self" in rule and row["matched"]:
            own = {"seconds": _as_float(row["self"], "self seconds"), "evidence_state": "Measured"}
        calls = row["calls"] if "calls" in rule and row["matched"] else None
        correctness = "not_checked"
        if "correctness" in rule and row["correctness"]:
            correctness = "failed" if "failed" in row["correctness"] else (
                "passed" if all(x == "passed" for x in row["correctness"]) else "not_checked")
        work = {}
        if row["matched"]:
            for name, spec in rule.get("work", {}).items():
                work[name] = {"value": _as_float(row["work"][name], "work"), "unit": spec["unit"],
                              "evidence_state": spec["evidence_state"]}
        stages.append({"id": rule["id"], "name": rule["name"], "parent_id": rule["parent_id"],
                       "inclusive": inclusive, "self": own, "calls": calls, "correctness": correctness,
                       "children_coverage": rule["children_coverage"], "work": work,
                       "source_lines": {"count": row["rows"], "first": row["first"], "last": row["last"]}})
    result = {"schema_version": 1, "representation": "summary", "scope": config["scope"], "stages": stages,
              "source": {"input_sha256": digest, "mapping_sha256": mapping_digest, "input_lines": input_lines,
                         "matched_lines": matched_lines, "ignored_lines": ignored_lines,
                         "source_path": str(source), "mapping_path": str(mapping_path)}}
    if sha256(mapping_path) != mapping_digest:
        raise LabError("storage_failed", "Phase mapping changed before summary publication")
    write_json(output, result)
    return {"output": str(output), "source": result["source"], "stages": len(stages), "matched_lines": matched_lines,
            "ignored_lines": ignored_lines}
