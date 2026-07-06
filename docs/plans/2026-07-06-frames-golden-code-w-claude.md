# Golden-раскадровка: лекция «Code w/ Claude — Picking the Right Model» (31:11)

Эталон для калибровки стадии кадров. Составлен вручную по контактным листам
(кадр/20 с) и сверен с плато-анализом сигналов (2026-07-06). Видео: 720p h264,
монтаж чередует полноэкранные врезки слайдов и общий план сцены со спикером;
внизу кадра — вшитые субтитры (edge case, см. фикс субтитровой полосы).

Файл лекции (локально): `C:\Users\koskr\Downloads\Telegram Desktop\video_2026-07-06_22-50-08.mp4`
SRT: транскрипт задачи `be0d87980829422f87e8a62538d1a276`.

## Эталонный набор кадров (идеальная выдача ≈ 17–19 кадров)

| № | ts (лучший) | Слайд | Источник | Прогон 06.07 |
|---|---|---|---|---|
| 1 | 0:03 | «A new model just dropped. Should you switch?» | fullscreen 0:00–0:20 | ✗ пропущен |
| 2 | ~1:25 | «It's a simple problem right?» (Haiku/Sonnet/Opus) | только экран на сцене | ✗ пропущен |
| 3 | ~2:30 | «…but what about effort levels?» | только экран на сцене | ✗ пропущен |
| 4 | 3:00 | «Three things to take away» | fullscreen (повтор 24:20) | ✗ пропущен |
| 5 | 4:03 | «SWE-bench, GPQA, BrowseComp as priors, not verdicts» | fullscreen | ✗ пропущен |
| 6 | 5:27 | «A well-defined task» | fullscreen | ✗ пропущен |
| 7 | 8:20 | «Common gotchas when running evals» (титульник) | экран на сцене | ✗ пропущен |
| 8 | 8:47 | «Most surprising eval results are bugs in the eval» | fullscreen 8:40–10:40 | ✓ slide-01 (9:18) |
| 9 | 12:01 | «A few lessons learned: read the transcript» | fullscreen | ✗ пропущен |
| 10 | 13:29 | «Choosing the right model and the right config» (титульник) | экран на сцене | ✗ пропущен |
| 11 | 14:13 | «Story of an internal eval for a simple code-fix pipeline» (scatter) | экран на сцене | ✗ пропущен |
| 12 | 15:20 | «Thinking and effort» | fullscreen | ✗ пропущен |
| 13 | 16:44 | «When the bigger model became the cheaper one» | fullscreen 16:40 | ~ slide-02 (17:03, взят с общего плана) |
| 14 | 18:09 | «Prompt caching and context hygiene move the whole frontier» | fullscreen 18:00–18:20 | ~ slide-03 (19:23, с общего плана) |
| 15 | 21:33 | «Improve token efficiency of tool responses» | fullscreen 21:40–22:00 | ✗ пропущен |
| 16 | 24:19 | «Three things to take away» (повтор; допустимо схлопнуть с №4 в дедупе) | fullscreen | ✗ пропущен |
| 17 | 25:25 | «Workshop» (титульник, тёмный) | экран на сцене | ✓ slide-04 (25:48) |
| 18 | 26:48 и 27:38 | скринкаст VS Code/терминал (TauBench setup) — 1–2 точки остановки | fullscreen скринкаст 26:00–27:45 | ✗ пропущен (code-режим) |
| 19 | 28:10 и ~29:30 | «tau2-bench airline sweep» таблица + sweep-графики pass rate vs cost/latency | fullscreen скринкаст | ~ slide-05 (29:09, один из двух) |

Легенда: ✓ взят корректно; ~ взят, но неоптимальный вариант/момент; ✗ пропущен.

## Метрики прогона 2026-07-06 (до фиксов)

- Выдано 5 кадров из ~17 эталонных → recall ≈ 30–35 %. Мусора нет (precision 100 %).
- 3 из 5 кадров взяты с общего плана сцены при наличии полноэкранной врезки рядом.
- Все кадры grayscale (баг decode_gray в выемке).
- Причина недобора: вшитые субтитры рвут mad-плато (подтверждено: mad по верхним
  80 % кадра даёт 31 плато со стартами, совпадающими с этой таблицей).

## Критерий после фиксов

Прогон `scripts/frames_debug.py` на этой лекции должен дать ≥ 14 из 17 эталонных
состояний (№2, №3, №7 — допустимые потери: короткие/дальний план), все кадры в
цвете, полноэкранные врезки предпочтены общим планам для №8, №13, №14.
