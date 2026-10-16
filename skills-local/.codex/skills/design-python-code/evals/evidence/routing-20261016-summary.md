# Native routing check (2026-10-16, Claude Code 2.1.292, model: opus)

目的: 機能レベルの依頼（「〜できない？」「〜して欲しい」）で Python の責務・置き場所が変わるとき、
最初のコード編集の前にこのスキルが呼ばれるようにする (description のみ改訂)。
きっかけ: kiku の作成・機能追加の会話 (2026-10-15〜16) で一度も呼ばれなかった。

方法: kiku を模した小さな fixture を毎回新しくコピーし、`claude -p --setting-sources project`
で対象スキルだけを project スキルとして置く (ユーザーのスキルは読み込まない)。
Skill 呼び出し / 最初の Edit・Write のどちらが先かを観測した時点で打ち切る。各 1 試行。

| ケース | 期待 | 旧 | 新 |
|---|---|---|---|
| feat-subtopic-slides | 呼ぶ | Edit が先 | 呼んだ |
| feat-system-audio | 呼ぶ | 呼ばない | 呼んだ |
| feat-glossary-diff | 呼ぶ | Edit が先 | 呼んだ |
| new-python-app | 呼ぶ | 呼んだ | 呼んだ |
| change-constant | 呼ばない | 呼ばない | 呼ばない |
| small-bug | 呼ばない | 呼ばない | 呼ばない |
| 既存 positive 4件 | 呼ぶ | (未実施) | 4/4 |
| 既存 negative / near-miss 8件 | 呼ばない | (未実施) | 8/8 |
| holdout: usage記録の追加 (固定後) | 呼ぶ | 呼んだ | 呼んだ |
| holdout: 定数変更 (固定後) | 呼ばない | 呼ばない | 呼ばない |

判断: 採用。機能レベルの依頼で 1/4 → 4/4、既存ケースと定数・小さな bug 修正で後退なし。
限界: 各 1 試行のみ (非決定性は未評価)。Codex のルーティングは未実施。fixture は小さく、
大きなリポジトリでの探索の長さによる影響は未確認。
