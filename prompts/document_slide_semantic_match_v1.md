Сопоставь ровно одну страницу только с предоставленными section и SRT block IDs.

Тематическое сходство без свидетельства недостаточно. Для explicit обязательны
точная цитата из указанных SRT blocks и термин/утверждение, присутствующее на
странице. Для strong требуется независимая последующая проверка. Если надёжного
свидетельства нет, верни semantic_tier `none`.

Верни ровно один JSON-объект, не массив и не Markdown:

{"slide_num": 1, "global_section_id": 0, "evidence_block_ids": [1],
"evidence_quote": "точная цитата", "semantic_tier": "explicit"}

Используй только эти пять имён полей. Не добавляй explanation, exact_quote,
confidence или другие поля. `slide_num`, `global_section_id` и block IDs копируй
из входа без переименования.
