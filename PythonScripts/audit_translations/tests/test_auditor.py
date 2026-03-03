"""
Tests for auditor helpers.
"""

from pathlib import Path

import pytest

from ..auditor import compare_files, console, get_yaml_files, list_languages
from ..dataclasses import ComparisonResult, RuleDifference, RuleInfo
from ..renderer import collect_issues, print_warnings


@pytest.fixture()
def fixed_console_width():
    """Pin Rich console to 80 columns so golden-file comparisons are portable."""
    old = console.width
    console.width = 80
    yield
    console.width = old


def make_rule(name: str, tag: str, line: int, raw: str) -> RuleInfo:
    return RuleInfo(
        name=name,
        tag=tag,
        key=f"{name}|{tag}",
        line_number=line,
        raw_content=raw,
    )


def test_collect_issues_fields() -> None:
    """Ensure collect issues fields."""
    missing = make_rule("missing", "mo", 10, "missing raw")
    extra = make_rule("extra", "mi", 20, "extra raw")
    untranslated = make_rule("untranslated", "mn", 30, "untranslated raw")
    diff_en = make_rule("diff", "mrow", 40, "diff en raw")
    diff_tr = make_rule("diff", "mrow", 41, "diff tr raw")

    diff = RuleDifference(
        english_rule=diff_en,
        translated_rule=diff_tr,
        diff_type="match",
        description="Match differs",
        english_snippet="a",
        translated_snippet="b",
    )

    result = ComparisonResult(
        missing_rules=[missing],
        extra_rules=[extra],
        untranslated_text=[(untranslated, [("t", "x", 31)])],
        rule_differences=[diff],
        file_path="",
        english_rule_count=1,
        translated_rule_count=1,
    )

    issues = collect_issues(result, "file.yaml", "xx")
    by_type = {issue["issue_type"]: issue for issue in issues}

    assert by_type["missing_rule"]["issue_line_en"] == 10
    assert by_type["missing_rule"]["issue_line_tr"] is None
    assert by_type["missing_rule"]["rule_line_en"] == 10
    assert by_type["missing_rule"]["rule_line_tr"] is None
    assert "english_raw" not in by_type["missing_rule"]

    assert by_type["extra_rule"]["issue_line_tr"] == 20
    assert by_type["extra_rule"]["rule_line_tr"] == 20
    assert "translated_raw" not in by_type["extra_rule"]

    assert by_type["untranslated_text"]["untranslated_texts"] == ["x"]
    assert by_type["untranslated_text"]["issue_line_tr"] == 31
    assert by_type["untranslated_text"]["rule_line_tr"] == 30
    assert "translated_raw" not in by_type["untranslated_text"]

    assert by_type["rule_difference"]["diff_type"] == "match"
    assert by_type["rule_difference"]["english_snippet"] == "a"
    assert by_type["rule_difference"]["translated_snippet"] == "b"
    assert by_type["rule_difference"]["issue_line_en"] == 40
    assert by_type["rule_difference"]["issue_line_tr"] == 41
    assert by_type["rule_difference"]["rule_line_en"] == 40
    assert by_type["rule_difference"]["rule_line_tr"] == 41
    assert "english_raw" not in by_type["rule_difference"]


def test_compare_files_merges_region_rules(tmp_path) -> None:
    """
    Ensures region rule files merge with base language before comparison.

    Confirms region overrides are recognized alongside base translations.
    Prevents false positives when content is split across directories.
    """
    rules_dir = tmp_path / "Rules" / "Languages"
    english_dir = rules_dir / "en"
    lang_dir = rules_dir / "zz"
    region_dir = lang_dir / "aa"
    english_dir.mkdir(parents=True)
    lang_dir.mkdir(parents=True)
    region_dir.mkdir(parents=True)

    english_file = english_dir / "base.yaml"
    translated_file = lang_dir / "base.yaml"
    translated_region_file = region_dir / "base.yaml"

    english_file.write_text(
        """- name: base-one
  tag: mo
  match: "."
  replace:
    - t: "one"
- name: base-two
  tag: mi
  match: "."
  replace:
    - t: "two"
""",
        encoding="utf-8",
    )

    translated_file.write_text(
        """- name: base-one
  tag: mo
  match: "."
  replace:
    - T: "eins"
""",
        encoding="utf-8",
    )

    translated_region_file.write_text(
        """- name: base-two
  tag: mi
  match: "."
  replace:
    - T: "zwei"
""",
        encoding="utf-8",
    )

    result = compare_files(
        str(english_file),
        str(translated_file),
        None,
        str(translated_region_file),
    )

    assert result.missing_rules == []
    assert result.extra_rules == []


def test_compare_files_skips_untranslated_and_diffs_when_audit_ignored(tmp_path) -> None:
    """
    Ensure audit-ignore suppresses untranslated and diff findings for a rule.

    The translated rule intentionally contains both a lowercase text key and a
    match mismatch. With an audit-ignore marker present, neither should be
    surfaced by compare_files.
    """
    english_file = tmp_path / "en.yaml"
    translated_file = tmp_path / "de.yaml"

    english_file.write_text(
        """- name: ignored-rule
  tag: mo
  match: "self::m:mo"
  replace:
    - T: "english"
""",
        encoding="utf-8",
    )
    translated_file.write_text(
        """- name: ignored-rule
  tag: mo  # audit-ignore
  match: "self::m:mi"
  replace:
    - t: "nicht uebersetzt"
""",
        encoding="utf-8",
    )

    result = compare_files(str(english_file), str(translated_file))

    assert result.missing_rules == []
    assert result.extra_rules == []
    assert result.untranslated_text == []
    assert result.rule_differences == []
    assert collect_issues(result, "de.yaml", "de") == []


def test_get_yaml_files_includes_region(tmp_path) -> None:
    """
    Ensures get_yaml_files merges base and region file lists.

    Verifies both root and SharedRules entries are discovered.
    Confirms region-only files are added with their own relative paths.
    """
    lang_dir = tmp_path / "lang"
    region_dir = lang_dir / "aa"
    (lang_dir / "SharedRules").mkdir(parents=True)
    region_dir.mkdir(parents=True)

    (lang_dir / "base.yaml").write_text("---", encoding="utf-8")
    (lang_dir / "SharedRules" / "shared.yaml").write_text("---", encoding="utf-8")
    (region_dir / "unicode.yaml").write_text("---", encoding="utf-8")

    files = get_yaml_files(lang_dir, region_dir)
    assert set(files) == {Path("base.yaml"), Path("SharedRules/shared.yaml"), Path("unicode.yaml")}


def test_list_languages_includes_region_codes(tmp_path) -> None:
    """
    Ensures list_languages reports region variants.

    Confirms region directories appear alongside base language entries.
    Protects CLI output from hiding available variants.
    """
    rules_dir = tmp_path / "Rules" / "Languages"
    (rules_dir / "en").mkdir(parents=True)
    lang_dir = rules_dir / "zz"
    region_dir = lang_dir / "aa"
    lang_dir.mkdir(parents=True)
    region_dir.mkdir(parents=True)

    (lang_dir / "file.yaml").write_text("---", encoding="utf-8")
    (region_dir / "region.yaml").write_text("---", encoding="utf-8")

    with console.capture() as capture:
        list_languages(str(rules_dir))
    output = capture.get()

    assert "zz" in output
    assert "zz-aa" in output


def test_print_warnings_omits_snippets_when_not_verbose(fixed_console_width) -> None:
    """
    Ensure the print_warnings output matches the non-verbose golden snapshot.

    Uses pytest fixture for console width.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"
    golden_path = base_dir / "golden" / "rich" / "structure_diff_nonverbose.golden"
    result = compare_files(
        str(fixtures_dir / "en" / "structure_diff.yaml"),
        str(fixtures_dir / "de" / "structure_diff.yaml"),
    )

    with console.capture() as capture:
        print_warnings(result, "structure_diff.yaml", verbose=False)
    output = capture.get()

    assert output == golden_path.read_text(encoding="utf-8")


def test_print_warnings_includes_snippets_when_verbose(fixed_console_width) -> None:
    """
    Ensure the print_warnings output matches the verbose golden snapshot.

    Uses pytest fixture for console width.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"
    golden_path = base_dir / "golden" / "rich" / "structure_diff_verbose.golden"
    result = compare_files(
        str(fixtures_dir / "en" / "structure_diff.yaml"),
        str(fixtures_dir / "de" / "structure_diff.yaml"),
    )

    with console.capture() as capture:
        print_warnings(result, "structure_diff.yaml", verbose=True)
    output = capture.get()

    assert output == golden_path.read_text(encoding="utf-8")


def test_misaligned_structure_differences_are_reported() -> None:
    """
    Test that structure differences with misaligned tokens are reported.

    When English has a "test" block that Norwegian doesn't have (and vice versa),
    the structural tokens become misaligned. We still report this as a structure
    issue and anchor it to the divergence position.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"

    result = compare_files(
        str(fixtures_dir / "en" / "structure_misaligned.yaml"),
        str(fixtures_dir / "de" / "structure_misaligned.yaml"),
    )

    # The result should detect that structures differ
    assert len(result.rule_differences) > 0
    assert any(diff.diff_type == "structure" for diff in result.rule_differences)

    # Collecting issues should include a structure issue.
    issues = collect_issues(result, "structure_misaligned.yaml", "de")
    structure_issues = [i for i in issues if i["diff_type"] == "structure"]

    assert len(structure_issues) == 1
    issue = structure_issues[0]
    assert issue["issue_line_en"] == 11
    assert issue["issue_line_tr"] == 11

    # Other differences (like conditions) should still be reported
    condition_issues = [i for i in issues if i["diff_type"] == "condition"]
    assert len(condition_issues) > 0, "Expected condition differences to still be reported"


def test_missing_else_block_is_still_reported() -> None:
    """
    Test that legitimate missing structural elements are still reported.

    When one file has an 'else' block and the other doesn't, this is a clear
    structural difference that should be reported with accurate line numbers.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"

    result = compare_files(
        str(fixtures_dir / "en" / "structure_missing_else.yaml"),
        str(fixtures_dir / "de" / "structure_missing_else.yaml"),
    )

    # Should detect structure difference
    assert len(result.rule_differences) > 0
    structure_diffs = [diff for diff in result.rule_differences if diff.diff_type == "structure"]
    assert len(structure_diffs) == 1

    # This case has one token None (missing else), so it should still be reported
    issues = collect_issues(result, "structure_missing_else.yaml", "de")
    structure_issues = [i for i in issues if i["diff_type"] == "structure"]

    # CRITICAL: This legitimate difference should still be reported
    # One file has else:, the other doesn't - a clear missing element
    assert len(structure_issues) == 1, (
        "Expected missing else block to be reported, "
        f"but found {len(structure_issues)} structure issues"
    )

    # Verify the issue anchors to the last shared structure token ('then:')
    issue = structure_issues[0]
    assert issue["issue_line_en"] == 7
    assert issue["issue_line_tr"] == 7


def test_structure_diff_uses_position_aware_token_occurrence_for_missing_block(tmp_path) -> None:
    """
    Structure diffs with repeated tokens should anchor at the divergence point.

    English contains an additional second `test` block. The first mismatch is
    after the first `then:`, so both sides should anchor to that shared `then:`
    line (not to the first `test:` line or the top of the rule).
    """
    english_file = tmp_path / "en.yaml"
    translated_file = tmp_path / "tr.yaml"
    english_file.write_text(
        """- name: repeated-structure
  tag: root
  match: "."
  replace:
    - test:
        if: a
        then: [T: "x"]
    - test:
        if: b
        then: [T: "y"]
""",
        encoding="utf-8",
    )
    translated_file.write_text(
        """- name: repeated-structure
  tag: root
  match: "."
  replace:
    - test:
        if: a
        then: [T: "x"]
""",
        encoding="utf-8",
    )

    result = compare_files(str(english_file), str(translated_file))
    issues = collect_issues(result, "repeated-structure.yaml", "tr")
    structure_issues = [i for i in issues if i["diff_type"] == "structure"]

    assert len(structure_issues) == 1
    issue = structure_issues[0]
    assert issue["issue_line_en"] == 7
    assert issue["issue_line_tr"] == 7


def test_structure_substitution_diff_is_reported(tmp_path) -> None:
    """
    Structural token substitutions should be reported as structure issues.
    """
    english_file = tmp_path / "en.yaml"
    translated_file = tmp_path / "tr.yaml"
    english_file.write_text(
        """- name: substitution-structure
  tag: root
  match: "."
  replace:
    - test:
        if: a
        then: [T: "x"]
""",
        encoding="utf-8",
    )
    translated_file.write_text(
        """- name: substitution-structure
  tag: root
  match: "."
  replace:
    - test:
        if: a
        else: [T: "x"]
""",
        encoding="utf-8",
    )

    result = compare_files(str(english_file), str(translated_file))
    assert any(diff.diff_type == "structure" for diff in result.rule_differences)

    issues = collect_issues(result, "substitution-structure.yaml", "tr")
    structure_issues = [i for i in issues if i["diff_type"] == "structure"]
    assert len(structure_issues) == 1
    issue = structure_issues[0]
    assert issue["issue_line_en"] == 7
    assert issue["issue_line_tr"] == 7


def test_structure_per_fraction_should_anchor_to_replace_lines_expected_behavior() -> None:
    """
    Expected behavior: structure differences should point to the `replace:` line.

    This uses real `per-fraction` rules extracted from
    `en/SimpleSpeak_Rules.yaml` and `nb/SimpleSpeak_Rules.yaml`.
    In both fixtures, `replace:` is on line 8.
    """
    base_dir = Path(__file__).parent
    path = base_dir / "fixtures" / "repro"
    result = compare_files(str(path / "en" / "per_fraction.yaml"), str(path / "nb" / "per_fraction.yaml"))

    issues = collect_issues(result, "per_fraction.yaml", "nb")
    structure_issues = [i for i in issues if i["diff_type"] == "structure"]

    assert len(structure_issues) == 1
    issue = structure_issues[0]
    assert issue["issue_line_en"] == 8
    assert issue["issue_line_tr"] == 8


def test_print_warnings_shows_misaligned_structures() -> None:
    """
    Test that print_warnings displays misaligned structure differences.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"

    result = compare_files(
        str(fixtures_dir / "en" / "structure_misaligned.yaml"),
        str(fixtures_dir / "de" / "structure_misaligned.yaml"),
    )

    # Raw result should have structure differences detected
    structure_diffs = [diff for diff in result.rule_differences if diff.diff_type == "structure"]
    assert len(structure_diffs) > 0

    with console.capture() as capture:
        issues_count = print_warnings(result, "structure_misaligned.yaml", verbose=False)
    output = capture.get()

    # Misaligned structure differences should be rendered.
    assert "Rule structure differs" in output, (
        "Expected misaligned structure differences to be shown in display"
    )

    # The issues count should include both condition + structure differences.
    condition_diffs = [diff for diff in result.rule_differences if diff.diff_type == "condition"]
    structure_diffs = [diff for diff in result.rule_differences if diff.diff_type == "structure"]
    assert issues_count == len(condition_diffs) + len(structure_diffs), (
        f"Expected issues_count ({issues_count}) to include condition+structure diffs"
    )


def test_print_warnings_still_shows_missing_else() -> None:
    """
    Test that print_warnings still displays legitimate missing elements.
    """
    base_dir = Path(__file__).parent
    fixtures_dir = base_dir / "fixtures"

    result = compare_files(
        str(fixtures_dir / "en" / "structure_missing_else.yaml"),
        str(fixtures_dir / "de" / "structure_missing_else.yaml"),
    )

    with console.capture() as capture:
        issues_count = print_warnings(result, "structure_missing_else.yaml", verbose=False)
    output = capture.get()

    # CRITICAL: This legitimate difference should appear in output
    assert "Rule structure differs" in output, (
        "Expected missing else block to be shown in output"
    )

    # Should report exactly 1 issue (the structure difference)
    assert issues_count == 1, f"Expected 1 issue but got {issues_count}"


def test_print_warnings_groups_multiple_subgroups_for_single_rule(fixed_console_width) -> None:
    """
    Ensure one rule can render multiple subgroup types in stable order.

    This covers the new grouped renderer path where a single rule can include
    untranslated entries plus multiple diff types. It validates both subgroup
    labels and the expected ordering policy.
    """
    en = make_rule("grouped-rule", "mi", 10, "en raw")
    tr = make_rule("grouped-rule", "mi", 20, "tr raw")
    en.line_map = {"match": [11], "condition": [12], "variables": [13]}
    tr.line_map = {"match": [21], "condition": [22], "variables": [23]}

    diffs = [
        RuleDifference(
            english_rule=en,
            translated_rule=tr,
            diff_type="match",
            description="Match pattern differs",
            english_snippet="en-match",
            translated_snippet="tr-match",
        ),
        RuleDifference(
            english_rule=en,
            translated_rule=tr,
            diff_type="condition",
            description="Conditions differ",
            english_snippet="en-cond",
            translated_snippet="tr-cond",
        ),
        RuleDifference(
            english_rule=en,
            translated_rule=tr,
            diff_type="variables",
            description="Variable definitions differ",
            english_snippet="en-var",
            translated_snippet="tr-var",
        ),
    ]
    result = ComparisonResult(
        missing_rules=[],
        extra_rules=[],
        untranslated_text=[(tr, [("t", "first", 24), ("ct", "second", 25)])],
        rule_differences=diffs,
        file_path="",
        english_rule_count=1,
        translated_rule_count=1,
    )

    with console.capture() as capture:
        issues_count = print_warnings(result, "grouped.yaml", verbose=False)
    output = capture.get()

    assert output.count("• grouped-rule (mi)") == 1
    assert "Untranslated Text [2]" in output
    assert "Match Pattern Differences [1]" in output
    assert "Condition Differences [1]" in output
    assert "Variable Differences [1]" in output

    untranslated_index = output.index("Untranslated Text [2]")
    match_index = output.index("Match Pattern Differences [1]")
    condition_index = output.index("Condition Differences [1]")
    variable_index = output.index("Variable Differences [1]")
    assert untranslated_index < match_index < condition_index < variable_index

    assert issues_count == 5


def test_print_warnings_groups_missing_and_extra_by_rule(fixed_console_width) -> None:
    """
    Ensure missing, extra, and diff issues are grouped under their own rule headers.

    This verifies grouping across multiple rules: each rule should appear once,
    with only its relevant subgroup block(s), and issue counting should remain
    aligned with rendered leaf items.
    """
    missing = make_rule("missing-rule", "mn", 30, "missing raw")
    extra = make_rule("extra-rule", "mo", 40, "extra raw")
    en = make_rule("diff-rule", "mrow", 50, "diff en")
    tr = make_rule("diff-rule", "mrow", 60, "diff tr")
    en.line_map = {"condition": [51]}
    tr.line_map = {"condition": [61]}
    diff = RuleDifference(
        english_rule=en,
        translated_rule=tr,
        diff_type="condition",
        description="Conditions differ",
        english_snippet="en-only",
        translated_snippet="tr-only",
    )

    result = ComparisonResult(
        missing_rules=[missing],
        extra_rules=[extra],
        untranslated_text=[],
        rule_differences=[diff],
        file_path="",
        english_rule_count=2,
        translated_rule_count=2,
    )

    with console.capture() as capture:
        issues_count = print_warnings(result, "mixed.yaml", verbose=False)
    output = capture.get()

    assert output.count("• missing-rule (mn)") == 1
    assert output.count("• extra-rule (mo)") == 1
    assert output.count("• diff-rule (mrow)") == 1
    assert "Missing in Translation [1]" in output
    assert "Extra in Translation [1]" in output
    assert "Condition Differences [1]" in output
    assert issues_count == 3


def test_print_warnings_verbose_shows_snippets_only_for_differences(fixed_console_width) -> None:
    """
    Ensure verbose snippet lines are printed only for rule differences.

    Missing and untranslated groups should not emit en/tr snippet lines in
    verbose mode; only diff subgroups should include these details.
    """
    missing = make_rule("missing-rule", "mn", 10, "missing raw")
    tr_untranslated = make_rule("untranslated-rule", "mi", 20, "untranslated raw")
    en = make_rule("diff-rule", "mrow", 30, "diff en")
    tr = make_rule("diff-rule", "mrow", 40, "diff tr")
    en.line_map = {"match": [31]}
    tr.line_map = {"match": [41]}

    diff = RuleDifference(
        english_rule=en,
        translated_rule=tr,
        diff_type="match",
        description="Match pattern differs",
        english_snippet="en-snippet",
        translated_snippet="tr-snippet",
    )
    result = ComparisonResult(
        missing_rules=[missing],
        extra_rules=[],
        untranslated_text=[(tr_untranslated, [("t", "leave me", 21)])],
        rule_differences=[diff],
        file_path="",
        english_rule_count=2,
        translated_rule_count=2,
    )

    with console.capture() as capture:
        issues_count = print_warnings(result, "verbose.yaml", verbose=True)
    output = capture.get()

    assert "Missing in Translation [1]" in output
    assert "Untranslated Text [1]" in output
    assert "Match Pattern Differences [1]" in output
    assert output.count("en:") == 1
    assert output.count("tr:") == 1
    assert "en-snippet" in output
    assert "tr-snippet" in output
    assert issues_count == 3
