"""
Auditing and comparison logic.

Contains functions for comparing English and translated files,
and for performing full language audits.
"""

import json
import sys
from pathlib import Path
from typing import List, Optional, TextIO, Tuple

from rich.panel import Panel
from rich.table import Table

from .dataclasses import RuleInfo, ComparisonResult
from .parsers import parse_yaml_file, diff_rules
from .renderer import collect_issues, console, print_warnings

# Re-export console so existing `from .auditor import console` callers keep working.
__all__ = ["console"]


def normalize_language(language: str) -> str:
    """Return a normalized language code (lowercase, '-' separators)."""
    return language.lower().replace("_", "-")


def split_language(language: str) -> Tuple[str, Optional[str]]:
    """Split a language code into base and optional region."""
    normalized = normalize_language(language)
    if "-" in normalized:
        base, region = normalized.split("-", 1)
        return base, region or None
    return normalized, None


def get_rules_dir(rules_dir: Optional[str] = None) -> Path:
    """Get the Rules/Languages directory path"""
    if rules_dir:
        return Path(rules_dir).expanduser()
    # Navigate from the package directory to the Rules directory
    package_dir = Path(__file__).parent
    return package_dir.parent.parent / "Rules" / "Languages"


def get_yaml_files(lang_dir: Path, region_dir: Optional[Path] = None) -> List[Path]:
    """Get all YAML files to audit for a language, including region overrides."""
    files: set[Path] = set()

    def collect_from(directory: Path, root: Path) -> None:
        if not directory.exists():
            return
        for f in directory.glob("*.yaml"):
            if f.name != "prefs.yaml":  # Skip prefs.yaml as it's not translated
                files.add(f.relative_to(root))
        shared_dir = directory / "SharedRules"
        if shared_dir.exists():
            for f in shared_dir.glob("*.yaml"):
                files.add(f.relative_to(root))

    collect_from(lang_dir, lang_dir)
    if region_dir:
        collect_from(region_dir, region_dir)

    return sorted(files)


def compare_files(
    english_path: str,
    translated_path: str,
    issue_filter: Optional[set[str]] = None,
    translated_region_path: Optional[str] = None,
    english_region_path: Optional[str] = None,
) -> ComparisonResult:
    """Compare English and translated YAML files"""

    def load_rules(path: Optional[str]) -> List[RuleInfo]:
        if path and Path(path).exists():
            rules, _ = parse_yaml_file(path)
            return rules
        return []

    def merge_rules(base_rules: List[RuleInfo], region_rules: List[RuleInfo]) -> List[RuleInfo]:
        if not region_rules:
            return base_rules
        merged = {r.key: r for r in base_rules}
        for rule in region_rules:
            merged[rule.key] = rule
        return list(merged.values())

    english_rules = merge_rules(
        load_rules(english_path),
        load_rules(english_region_path),
    )
    translated_rules = merge_rules(
        load_rules(translated_path),
        load_rules(translated_region_path),
    )

    # Create lookup dictionaries
    english_by_key = {r.key: r for r in english_rules}
    translated_by_key = {r.key: r for r in translated_rules}

    include_all = issue_filter is None
    include_missing = include_all or "missing" in issue_filter
    include_untranslated = include_all or "untranslated" in issue_filter
    include_extra = include_all or "extra" in issue_filter
    include_diffs = include_all or "diffs" in issue_filter

    # Find missing rules (in English but not in translation)
    missing_rules = []
    if include_missing:
        for key, rule in english_by_key.items():
            if key not in translated_by_key:
                missing_rules.append(rule)

    # Find extra rules (in translation but not in English)
    extra_rules = []
    if include_extra:
        for key, rule in translated_by_key.items():
            if key not in english_by_key:
                extra_rules.append(rule)

    # Find untranslated text in translated file (skip if audit-ignore)
    untranslated_text = []
    if include_untranslated:
        for rule in translated_rules:
            if rule.has_untranslated_text and not rule.audit_ignore:
                untranslated_text.append((rule, rule.untranslated_entries))

    # Find fine-grained differences in rules that exist in both files (skip if audit-ignore)
    rule_differences = []
    if include_diffs:
        for key, en_rule in english_by_key.items():
            if key in translated_by_key:
                tr_rule = translated_by_key[key]
                if not tr_rule.audit_ignore:
                    diffs = diff_rules(en_rule, tr_rule)
                    rule_differences.extend(diffs)

    return ComparisonResult(
        missing_rules=missing_rules,
        extra_rules=extra_rules,
        untranslated_text=untranslated_text,
        rule_differences=rule_differences,
        file_path=translated_path,
        english_rule_count=len(english_rules),
        translated_rule_count=len(translated_rules)
    )


def audit_language(
    language: str,
    specific_file: Optional[str] = None,
    output_format: str = "rich",
    output_path: Optional[str] = None,
    rules_dir: Optional[str] = None,
    issue_filter: Optional[set[str]] = None,
    verbose: bool = False,
) -> int:
    """Audit translations for a specific language. Returns total issue count."""
    rules_dir_path = get_rules_dir(rules_dir)
    english_dir = rules_dir_path / "en"

    base_language, region = split_language(language)
    translated_dir = rules_dir_path / base_language
    translated_region_dir = translated_dir / region if region else None
    english_region_dir = english_dir / region if region else None

    if not english_dir.exists():
        console.print(f"\n[red]✗ Error:[/] English rules directory not found: {english_dir}")
        sys.exit(1)

    if not translated_dir.exists():
        console.print(f"\n[red]✗ Error:[/] Translation directory not found: {translated_dir}")
        sys.exit(1)

    if region and not (translated_region_dir and translated_region_dir.exists()):
        console.print(f"\n[red]✗ Error:[/] Region directory not found: {translated_region_dir}")
        sys.exit(1)

    # Get list of files to audit
    files = [specific_file] if specific_file else get_yaml_files(english_dir, english_region_dir)

    if output_format == "rich":
        # Print header
        console.print(Panel(f"MathCAT Translation Audit: {language.upper()}", style="bold cyan"))
        console.print(f"\n  [dim]Comparing against English (en) reference files[/]")
        console.print(f"  [dim]Files to check: {len(files)}[/]")

    out_stream: TextIO = sys.stdout
    if output_path:
        out_stream = open(output_path, "w", encoding="utf-8", newline="")

    total_issues = 0
    total_missing = 0
    total_untranslated = 0
    total_extra = 0
    total_differences = 0
    files_with_issues = 0
    files_ok = 0

    for file_name in files:
        english_path = english_dir / file_name
        translated_path = translated_dir / file_name
        translated_region_path = translated_region_dir / file_name if translated_region_dir else None
        english_region_path = english_region_dir / file_name if english_region_dir else None

        if not english_path.exists():
            console.print(f"\n[yellow]⚠ Warning:[/] English file not found: {english_path}")
            continue

        result = compare_files(
            str(english_path),
            str(translated_path),
            issue_filter,
            str(translated_region_path) if translated_region_path and translated_region_path.exists() else None,
            str(english_region_path) if english_region_path and english_region_path.exists() else None,
        )

        if output_format == "rich":
            if result.has_issues:
                issues = print_warnings(result, file_name, verbose, language)
                if issues > 0:
                    files_with_issues += 1
                total_issues += issues
            else:
                files_ok += 1
        else:
            issues_list = collect_issues(result, file_name, language)
            for issue in issues_list:
                out_stream.write(json.dumps(issue, ensure_ascii=False) + "\n")
            if issues_list:
                files_with_issues += 1
                total_issues += len(issues_list)
            else:
                files_ok += 1

        total_missing += len(result.missing_rules)
        total_untranslated += sum(len(entries) for _, entries in result.untranslated_text)
        total_extra += len(result.extra_rules)
        total_differences += len(result.rule_differences)

    if output_format == "rich":
        # Summary
        table = Table(title="SUMMARY", title_style="bold", box=None, show_header=False, padding=(0, 2))
        table.add_column(width=30)
        table.add_column()
        for label, value, color in [
            ("Files checked", len(files), None),
            ("Files with issues", files_with_issues, "yellow" if files_with_issues else "green"),
            ("Files OK", files_ok, "green" if files_ok else None),
            ("Missing rules", total_missing, "red" if total_missing else "green"),
            ("Untranslated text", total_untranslated, "yellow" if total_untranslated else "green"),
            ("Rule differences", total_differences, "magenta" if total_differences else "green"),
            ("Extra rules", total_extra, "blue" if total_extra else None),
        ]:
            table.add_row(label, f"[{color}]{value}[/]" if color else str(value))
        console.print(Panel(table, style="cyan"))

    if output_path:
        out_stream.close()
    return total_issues


def list_languages(rules_dir: Optional[str] = None):
    """List available languages for auditing"""
    console.print(Panel("Available Languages", style="bold cyan"))

    table = Table(show_header=True, header_style="dim")
    table.add_column("Language", justify="center", style="cyan")
    table.add_column("YAML files", justify="right")

    rules_dir_path = get_rules_dir(rules_dir)
    for lang_dir in sorted(rules_dir_path.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name == "en":
            continue
        base_count = len(get_yaml_files(lang_dir))
        color = "green" if base_count >= 7 else "yellow" if base_count >= 4 else "red"
        table.add_row(lang_dir.name, f"[{color}]{base_count}[/] files")

        for region_dir in sorted(lang_dir.iterdir()):
            if region_dir.is_dir():
                code = f"{lang_dir.name}-{region_dir.name}"
                count = len(get_yaml_files(lang_dir, region_dir))
                region_color = "green" if count >= 7 else "yellow" if count >= 4 else "red"
                table.add_row(code, f"[{region_color}]{count}[/] files")

    console.print(table)
    console.print("\n  [dim]Reference: en (English) - base translation[/]\n")
