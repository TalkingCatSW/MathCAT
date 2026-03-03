"""
YAML file parsing functions.

Handles parsing of rule files and unicode files to extract rule information.
"""

import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from jsonpath_ng.ext import parse
from jsonpath_ng.jsonpath import Fields
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from .dataclasses import RuleInfo, RuleDifference


def is_unicode_file(file_path: str) -> bool:
    """Check if this is a unicode.yaml or unicode-full.yaml file"""
    basename = os.path.basename(file_path)
    return basename in ("unicode.yaml", "unicode-full.yaml")


def parse_yaml_file(file_path: str, strict: bool = False) -> Tuple[List[RuleInfo], str]:
    """
    Parse a YAML file and extract rules.
    Returns list of RuleInfo and the raw file content.

    For standard rule files: extracts rules with name/tag
    For unicode files: extracts entries with character/range keys
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        data = yaml.load(content)
    except ScannerError as exc:
        if strict:
            raise exc
        if "\t" in content:
            sanitized = content.replace("\t", "    ")
            data = yaml.load(sanitized)
        else:
            raise exc

    if is_unicode_file(file_path):
        rules = parse_unicode_file(content, data)
    else:
        rules = parse_rules_file(content, data)

    return rules, content



def format_tag(tag_value: Any) -> Optional[str]:
    if tag_value is None:
        return None
    if isinstance(tag_value, list):
        normalized = sorted(str(item).strip() for item in tag_value)
        return "[" + ", ".join(normalized) + "]"
    return str(tag_value)


def build_raw_blocks(lines: List[str], starts: List[int]) -> List[str]:
    blocks = []
    if not starts:
        return blocks
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]))
    return blocks


def mapping_key_line(mapping: Any, key: str) -> Optional[int]:
    """
    - 'lc' is line and column in YAML file: https://yaml.dev/doc/ruamel.yaml/detail/
    """
    if hasattr(mapping, "lc") and hasattr(mapping.lc, "data"):
        line_info = mapping.lc.data.get(key)
        return line_info[0] + 1
    return None


def iter_field_matches(node: Any) -> Iterator[Tuple[str, Any, Any]]:
    """
    Iterate nested mapping fields using jsonpath.

    Returns tuples of (key, child_value, parent_mapping) in traversal order.
    """
    all_fields_expr = parse('$..*')  # '..' is recursive descent

    for match in all_fields_expr.find(node):
        path = match.path
        if isinstance(path, Fields) and len(path.fields) == 1:
            key = path.fields[0]
            parent = match.context.value if match.context is not None else None
            yield key, match.value, parent


def parse_rules_file(content: str, data: Any) -> List[RuleInfo]:
    """Parse a standard rules file with name/tag entries"""
    if not isinstance(data, list):
        return []

    rules: List[RuleInfo] = []
    lines = content.splitlines()

    start_lines: List[int] = []
    rule_items: List[Any] = []
    for idx, item in enumerate(data):
        if isinstance(item, dict) and "name" in item:
            line = data.lc.item(idx)[0] if hasattr(data, "lc") else 0
            start_lines.append(line)
            rule_items.append(item)

    raw_blocks = build_raw_blocks(lines, start_lines)

    for item, raw_content, line_idx in zip(rule_items, raw_blocks, start_lines):
        rule_name = str(item.get("name"))
        tag = format_tag(item.get("tag"))
        rule_key = f"{rule_name}|{tag or 'unknown'}"
        rules.append(RuleInfo(
            name=rule_name,
            tag=tag,
            key=rule_key,
            line_number=line_idx + 1,
            raw_content=raw_content,
            data=item,
            untranslated_entries=find_untranslated_text_entries(item),
            line_map=build_line_map(item),
            audit_ignore=has_audit_ignore(raw_content)
        ))

    return rules


def parse_unicode_file(content: str, data: Any) -> List[RuleInfo]:
    """Parse a unicode file with character/range keys"""
    if not isinstance(data, list):
        return []

    rules: List[RuleInfo] = []
    lines = content.splitlines()

    start_lines: List[int] = []
    entries: List[Tuple[str, Any]] = []
    for idx, item in enumerate(data):
        if isinstance(item, dict) and len(item) == 1:
            key = next(iter(item.keys()))
            value = item[key]
            line = data.lc.item(idx)[0] if hasattr(data, "lc") else 0
            start_lines.append(line)
            entries.append((str(key), value))

    raw_blocks = build_raw_blocks(lines, start_lines)

    for (char_key, value), raw_content, line_idx in zip(entries, raw_blocks, start_lines):
        rules.append(RuleInfo(
            name=None,
            tag=None,
            key=char_key,
            line_number=line_idx + 1,
            raw_content=raw_content,
            data=value,
            untranslated_entries=find_untranslated_text_entries(value),
            line_map=build_line_map(value),
            audit_ignore=has_audit_ignore(raw_content)
        ))

    return rules


def has_audit_ignore(content: str) -> bool:
    """Check if the rule content contains an audit-ignore comment"""
    return '# audit-ignore' in content


def find_untranslated_text_values(node: Any) -> List[str]:
    """
    Find lowercase text keys (t, ot, ct, spell, pronounce, ifthenelse) that should be uppercase in translations.
    Returns list of the untranslated text values found.
    """
    return [entry[1] for entry in find_untranslated_text_entries(node)]


def find_untranslated_text_entries(node: Any) -> List[Tuple[str, str, Optional[int]]]:
    """
    Find lowercase text keys (t, ot, ct, spell, pronounce, ifthenelse) and their line numbers.
    Returns list of (key, text, line_number) entries. Line number is 1-based when available.
    """
    entries: List[Tuple[str, str, Optional[int]]] = []
    translation_keys = {"t", "ot", "ct", "spell", "pronounce", "ifthenelse"}

    def should_add(text: str) -> bool:
        if not text.strip():
            return False
        if len(text) == 1 and not text.isalpha():
            return False
        if text.startswith('$') or text.startswith('@'):
            return False
        return True

    for key, child, parent in iter_field_matches(node):
        if (
            key.lower() in translation_keys
            and not key.isupper()
            and isinstance(child, str)
        ):
            if should_add(child):
                entries.append((key, child, mapping_key_line(parent, key)))
    return entries


def build_line_map(node: Any) -> Dict[str, List[int]]:
    """
    Build a mapping of rule element types to line numbers.
    Line numbers are 1-based. Missing elements are omitted.
    """
    line_map: Dict[str, List[int]] = {}
    structure_tokens = {
        "test",
        "if",
        "else_if",
        "then",
        "else",
        "then_test",
        "else_test",
        "with",
        "replace",
        "intent",
    }

    def add_line(kind: str, line: Optional[int]) -> None:
        if line is None:
            return
        line_map.setdefault(kind, []).append(line)

    for key, _, parent in iter_field_matches(node):
        if key == "match":
            add_line("match", mapping_key_line(parent, key))
        if key in ("if", "else_if"):
            add_line("condition", mapping_key_line(parent, key))
        if key == "variables":
            add_line("variables", mapping_key_line(parent, key))
        if key in structure_tokens:
            add_line(f"structure:{key}", mapping_key_line(parent, key))
    return line_map


def normalize_match(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def normalize_xpath(value: str) -> str:
    return " ".join(value.split())

def dedup_list(values: List[str]) -> List[str]:
    """
    Return a list without duplicates while preserving first-seen order.
    Originally, rule differences were stored as sets, losing their original order,
    which is not helpful and why it changed with the help of this function.

    Example:
        >>> dedup_list(["if:a", "if:b", "if:a"])
        ['if:a', 'if:b']
    """

    return list(dict.fromkeys(values)) # dict preserves insertion order (guaranteed in Python 3.7+)


def extract_match_pattern(rule_data: Any) -> str:
    if isinstance(rule_data, dict):
        matches = parse('$.match').find(rule_data)
        if matches:
            return normalize_match(matches[0].value)
    return ""


def extract_conditions(rule_data: Any) -> List[str]:
    """Extract all if/else conditions from a rule"""
    conditions: List[str] = []
    for key, child, _ in iter_field_matches(rule_data):
        if key in ("if", "else_if") and isinstance(child, str):
            conditions.append(child)
    return conditions


def extract_variables(rule_data: Any) -> List[Tuple[str, str]]:
    """Extract variable definitions from a rule"""
    variables: List[Tuple[str, str]] = []

    def add_from_value(value: Any) -> None:
        if isinstance(value, dict):
            for name, expr in value.items():
                variables.append((str(name), str(expr)))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for name, expr in item.items():
                        variables.append((str(name), str(expr)))

    for key, child, _ in iter_field_matches(rule_data):
        if key == "variables":
            add_from_value(child)
    return variables


def extract_structure_elements(rule_data: Any) -> List[str]:
    """Extract structural elements (test, with, replace blocks) ignoring text content"""
    elements: List[str] = []
    tokens = {"test", "if", "else_if", "then", "else", "then_test", "else_test", "with", "replace", "intent"}
    for key, _, _ in iter_field_matches(rule_data):
        if key in tokens:
            elements.append(f"{key}:")
    return elements


def diff_rules(english_rule: RuleInfo, translated_rule: RuleInfo) -> List[RuleDifference]:
    """
    Compare two rules and return fine-grained differences.
    Ignores text content differences (T/t values) but catches structural changes.
    """
    differences = []
    # Check match pattern differences
    en_match_raw = extract_match_pattern(english_rule.data)
    translated_match_raw = extract_match_pattern(translated_rule.data)
    en_match = normalize_xpath(en_match_raw)
    translated_match = normalize_xpath(translated_match_raw)
    if en_match != translated_match and en_match and translated_match:
        differences.append(RuleDifference(
            english_rule=english_rule,
            translated_rule=translated_rule,
            diff_type='match',
            description='Match pattern differs',
            english_snippet=en_match,
            translated_snippet=translated_match
        ))

    # Check condition differences
    en_conditions_raw = extract_conditions(english_rule.data)
    tr_conditions_raw = extract_conditions(translated_rule.data)
    en_conditions = [normalize_xpath(c) for c in en_conditions_raw]
    tr_conditions = [normalize_xpath(c) for c in tr_conditions_raw]
    if en_conditions != tr_conditions:
        # Find specific differences
        en_set, tr_set = set(en_conditions), set(tr_conditions)
        if en_set != tr_set:
            differences.append(RuleDifference(
                english_rule=english_rule,
                translated_rule=translated_rule,
                diff_type='condition',
                description='Conditions differ',
                english_snippet=', '.join(dedup_list(en_conditions)) or '(none)',
                translated_snippet=', '.join(dedup_list(tr_conditions)) or '(none)'
            ))

    # Check variable differences
    en_vars = extract_variables(english_rule.data)
    tr_vars = extract_variables(translated_rule.data)
    if en_vars != tr_vars:
        en_var_names = {v[0] for v in en_vars}
        tr_var_names = {v[0] for v in tr_vars}
        if en_var_names != tr_var_names:
            differences.append(RuleDifference(
                english_rule=english_rule,
                translated_rule=translated_rule,
                diff_type='variables',
                description='Variable definitions differ',
                english_snippet=', '.join(sorted(en_var_names)) or '(none)',
                translated_snippet=', '.join(sorted(tr_var_names)) or '(none)'
            ))

    # Check structural differences (test/if/then/else blocks)
    en_structure = extract_structure_elements(english_rule.data)
    tr_structure = extract_structure_elements(translated_rule.data)
    if en_structure != tr_structure:
        differences.append(RuleDifference(
            english_rule=english_rule,
            translated_rule=translated_rule,
            diff_type='structure',
            description='Rule structure differs (test/if/then/else blocks)',
            english_snippet=' '.join(en_structure),
            translated_snippet=' '.join(tr_structure)
        ))

    return differences
