from lecturelog.evaluation.artifacts import parse_markdown
from lecturelog.evaluation.language import (
    analyze_language,
    detect_document_language,
    language_findings,
)


def test_russian_prose_with_technical_terms_urls_and_identifiers_is_russian():
    analysis = analyze_language(
        "Система использует OpenRouter API и FastAPI middleware. Подробности доступны "
        "на https://openrouter.ai/docs, а вызов выполняет request_model_v2."
    )

    assert analysis.detected == "ru"
    assert not analysis.is_mixed


def test_short_english_technical_heading_is_ignored():
    analysis = analyze_language("Feature Creep", kind="heading")

    assert analysis.ignored
    assert analysis.detected is None


def test_code_and_markdown_url_are_ignored():
    assert analyze_language("print('this is a long english code expression')", kind="code").ignored
    analysis = analyze_language(
        "Подробное русское объяснение находится в документации "
        "[OpenRouter documentation](https://openrouter.ai/docs/reference/models)."
    )
    assert analysis.detected == "ru"


def test_isolated_full_english_block_is_major_finding():
    blocks = parse_markdown(
        "# Русская лекция\n\n"
        "Это достаточно длинный русский абзац, который задает основной язык всего конспекта.\n\n"
        "This entire paragraph was unexpectedly generated in English and breaks the "
        "language consistency of the lecture notes.\n\n"
        "Здесь продолжается подробное русское объяснение основной темы нашей лекции."
    )

    assert detect_document_language(blocks) == "ru"
    findings = language_findings(blocks)

    assert [finding.code for finding in findings] == ["unexpected_full_block_language"]
    assert findings[0].severity == "major"


def test_substantial_mixed_language_prose_is_reported():
    blocks = parse_markdown(
        "Это длинное русское объяснение темы, которое формирует основной язык документа и "
        "содержит достаточно слов для уверенного определения.\n\n"
        "Этот блок начинается по-русски и подробно объясняет подход, but then it suddenly "
        "continues with a complete English explanation containing many ordinary words."
    )

    assert "mixed_language_prose" in {finding.code for finding in language_findings(blocks)}

