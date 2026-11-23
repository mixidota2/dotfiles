---
name: handoff
description: セッション終了時のtk（tasukura）への進捗・判断・発見・次アクションの記録と監査、およびセッション開始・再開時（「続けて」「今日何やるんだっけ」「今日は終わりにするから記録しておいて」や /handoff）の引き継ぎ手順を実行する。Pi と Claude Code の両方で共通して使う。
---

# セッションの引き継ぎと tk 記録監査（handoff）

作業セッションを区切るとき（「今日は終わりにするから記録しておいて」「一旦tkに記録しておいて」「`/handoff`」）、または作業を再開・整理するとき（「続けて」「今日何やるんだっけ」「やること整理しよう」）に、新しいセッションへ文脈を欠落なく引き継ぐ。個別コマンドの詳細は `tk` スキルに従う。

## 1. セッション終了・区切り時の記録と監査

エージェントは次の順序で `tk` とリポジトリの状態を確認し、1回で通る正確なコマンドで記録する。

1. **現在の状態の把握**:
   - 対象タスクIDが未特定なら `tk list`（必要に応じて `tk list --all`）と `git status -s` を並列に実行する。
   - 対象タスクの既存記録を `tk show <task_id> --logs 5` で確認し、重複記録や古い `blocker` / `question` が残っていないか確かめる。
2. **進捗ログ（`tk log`）の記録**:
   - 実施した変更・テスト結果・コミットハッシュ（または未コミット状態）・次に再開するときの最初の1手をまとめて記録する。
   - 構文（`--summary` 必須）:
     ```bash
     tk log <task_id> --summary "<1行の結論・到達点>" --details "<実施内容・検証コマンドと結果・残課題>" --next-action "<次回最初にやること>"
     ```
   - 出力された `ID: 01...`（`log_id`）を控える。
3. **知識レコード（`tk record`）の昇格と監査**:
   - 今回のセッションで新たに確定した設計判断（`decision`）、裏付け済みの事実（`finding`）、ブロッカー（`blocker`）、未決の問い（`question`）、未検証の仮説（`hypothesis`）がある場合のみ、手順2で得た `<log_id>` を使って追加する（未検証の推測を `finding` にしない）。
   - 構文（`--kind`, `--log-id`, `--summary` の3つすべて必須）:
     ```bash
     tk record add <task_id> --kind <decision|finding|blocker|question|hypothesis> --log-id <log_id> --summary "<結論1行>" --details "<判断根拠・適用範囲・見直し条件>"
     ```
   - 既存のレコードが更新された場合は `tk record add ... --supersedes <old_record_id>` を使い、解消した `blocker` / `question` は `tk record resolve <record_id>`、不要になったものは `tk record obsolete <record_id>` で整理する（`tk record supersede` や `tk records` というサブコマンドは存在しない）。
4. **ステータスの整合確認**:
   - 未着手は `todo`、作業継続中は `in_progress`、大平さんの実機確認・レビュー待ちは `in_review`、完了条件をすべて満たした場合のみ `done` に `tk status <task_id> <status>` で合わせる。
   - バックグラウンド実行中のジョブがある場合は、終了見込み時刻または停止方法を併せて報告する。

## 2. セッション開始・再開時のコンテキスト復元

「続けて」「`<task_id>` これ進めて」「今日何やるんだっけ」と言われたときは、作業を始める前に以下を確認する（Pi の `[TK HANDOFF CONTEXT]` で既に注入済みの場合は再実行しない）。

1. `tk list` と対象タスクの `tk show <task_id> --logs 8`、および `git status -s`（必要なら `git worktree list`）を確認する。
2. タスクの `next`（次のアクション）、有効な `Active Decisions` / `Open Findings` / `Active Blockers`、未コミット差分や既存 worktree の状態を短く要約し、合意済みの設計判断を落とさずに作業へ入る。
