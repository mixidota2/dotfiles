---
name: design-adk-ai-app
description: Google ADK（Agent Development Kit）やLLMを組み込んだWebアプリケーション（FastAPI + Next.js等）のモジュール構造・層境界・状態同期を設計・実装・レビューするときに使う。チャットUIとダッシュボード等の画面状態が連動するアプリ（Shared-State Copilot / Agentic UI）、チャットと裏側の非同期ワークフローが共存するアプリ、エージェントがDBや外部ML推論APIにアクセスするアプリのコード設計で使う。Pythonの汎用5層設計原則は design-python-code、ADKの個別SDK API構文は google-agents-cli-adk-code と併用し、単なるプロンプト文面の調整やADKのCLIスキャフォールド操作だけには使わない。
---

# ADK・AI組み込みWebアプリケーションを設計する

Google ADK（またはLLMエージェント）をバックエンドに持つWebアプリケーションを、「非決定的なAIの推論・対話状態」と「決定論的な業務ロジック・DB・画面描画データ」が混ざらないように設計する。

## 既存スキルとの役割分担（重複させない境界）

本スキルは以下の2スキルと組み合わせて使う。他スキルが担う領域を重複して再定義しない。

- **Pythonの汎用5層設計・命名・変更局所性**: `design-python-code` に従う。本スキルはその5層（`models/`, `<domain_modules>/`, `infra/`, `use_cases/`, `api/`）の上に、ADKランタイムとAI特有の境界をどうマッピングするかを定める。
- **ADK SDKの具体的なクラス・メソッド構文**: `google-agents-cli-adk-code`（`references/adk-python.md`, `references/adk-python-workflows.md`, `references/recipes.md`）を参照する。
- **本スキルの担当領域**:
  1. `design-python-code` の5層に対する ADK（`Agent`, `Workflow`, `Runner`, `ToolContext`）の配置と依存方向
  2. チャット対話（`LlmAgent`）と裏側の非同期・バッチ処理（`Workflow` / `SequentialAgent` 等）の共存とユースケース共有
  3. ツール関数へのDB・外部ML推論クライアントの依存注入（DI）と `ToolContext` の分離
  4. チャット結果によるダッシュボード等の他UI要素の更新（`Event.actions.state_delta` による軽量状態同期と、REST APIによる大容量データ取得の分離）

---

## 1. 標準5層アーキテクチャへのADKマッピング

ADKのサンプルにある `agent.py` と `tools.py` だけのフラット構成をそのままWebアプリに持ち込むと、ツール関数内にSQLや外部ML API呼び出し、業務ルールが混入してテスト不能になる。`design-python-code` の5層に以下の通り配置する。

```text
backend/src/<package_name>/
├── models/                  # 1. 共有データモデル（Pydantic等。ADKに依存しない）
│   ├── domain.py            # 業務エンティティ・予測/集計結果モデル
│   └── ui_state.py          # 画面とエージェントが共有する軽量UI状態（フィルタ条件・選択中ID等）
│
├── <domain_modules>/        # 2. 専門モジュール群（純粋な計算・検証・変換。ADKもDBも知らない）
│   ├── forecasting/
│   └── analytics/
│
├── infra/                   # 3. インフラ層（外部I/Oの実装詳細を閉じ込める）
│   ├── db/                  # アプリケーションDBのリポジトリ実装
│   ├── ml_clients/          # 外部MLモデル推論APIのクライアント（リトライ・タイムアウト・型変換）
│   └── adk_session/         # ADK用のSessionService / ArtifactService初期化設定
│
├── use_cases/               # 4. ユースケース層（専門モジュールとinfraを束ねる業務手順）
│   ├── run_forecast.py      # 画面REST APIからも、ADKのToolからも、非同期Workerからも共通で呼ぶ
│   └── query_dashboard.py
│
├── agents/                  # 5a. AIオーケストレーション入口（ADK依存はここに閉じ込める）
│   ├── runtime.py           # App, Runner, SessionServiceのシングルトン組み立て
│   ├── tools/               # ToolContextから状態を読み、use_casesを呼んでstate_deltaを書く薄いアダプタ
│   ├── copilot_agent/       # 対話用（LlmAgent中心。adk webでも単体起動可能な単位）
│   └── background_workflows/# 裏側の非同期・定期処理用（Workflow / SequentialAgent / ParallelAgent中心）
│
└── api/                     # 5b. HTTP / ストリーミング入口（FastAPI等）
    ├── main.py              # lifespanでのRunner初期化
    ├── stream_adapter.py    # ADKのEvent(text, tool, state_delta)をフロントエンド向けSSE/AG-UIに変換
    └── routers/
        ├── copilot.py       # チャットSSEエンドポイント（agents/runtime経由でcopilot_agentを実行）
        ├── dashboard.py     # ダッシュボード描画用の高速REST API（AIを介さずuse_casesを直呼び）
        └── jobs.py          # 非同期AIワークフロー起動用エンドポイント・Webhook受け口
```

### 守るべき依存ルール
- `models/`, `<domain_modules>/`, `infra/`（`adk_session`除く）, `use_cases/` から **`google.adk` を一切 import しない**。
- 業務データの正本（Source of Truth）はアプリケーションDB（`infra/db/`）に置き、ADKの `session.state` を業務データベース代わりに使わない。

---

## 2. ツール設計とDI（DB・外部ML推論APIとの接続）

ADKのツール関数は、関数シグネチャの型ヒントからLLM用のJSONスキーマを自動生成する。そのため、以下の2つのルールを厳守する。

1. **ツール関数の中にDBクエリやML API呼び出しを直接書かない**:
   ツール関数は `agents/tools/` に置き、`use_cases/` を呼び出すだけの薄いアダプタにする。これにより、チャット経由のツール実行と、ダッシュボード画面のボタン操作（REST API）で全く同じ `use_cases/` を共有できる。
2. **インフラ依存（Service/DB/ML Client）はファクトリ関数で束縛し、リクエスト文脈は `ToolContext` から取る**:
   DBセッションファクトリや `UseCase` インスタンスをツール関数の引数に並べると、LLMの引数スキーマが壊れる。クロージャ（ファクトリ関数）で `UseCase` を注入し、テナントIDやユーザーID、現在の画面状態は ADK が自動注入する `tool_context: ToolContext` から取得する。

```python
from google.adk.tools import ToolContext
from <pkg>.use_cases.run_forecast import RunForecastUseCase

def create_forecast_tools(use_case: RunForecastUseCase) -> list:
    async def run_demand_forecast(
        category_code: str,
        horizon_days: int,
        tool_context: ToolContext,  # ADKが自動注入。docstringには記載しない
    ) -> dict:
        """指定カテゴリの需要予測を実行し、ダッシュボードの表示対象を更新します。

        Args:
            category_code: 予測対象カテゴリコード
            horizon_days: 予測日数
        """
        tenant_id = tool_context.state["user:tenant_id"]

        # 1. ADK非依存のUseCaseを実行（外部ML推論API呼び出し＋DB永続化）
        result = await use_case.execute(
            tenant_id=tenant_id,
            category_code=category_code,
            horizon_days=horizon_days,
        )

        # 2. 画面連動用の軽量ステート更新（Event.actions.state_deltaとしてフロントへ流れる）
        tool_context.state["dashboard_view"] = {
            "active_tab": "forecast",
            "category_code": category_code,
            "forecast_run_id": result.run_id,
        }

        # 3. LLMには会話応答に必要な要約値だけを返す（何千行もの時系列配列は返さない）
        return {
            "status": "success",
            "forecast_run_id": result.run_id,
            "summary": result.summary_metrics.model_dump(),
        }

    return [run_demand_forecast]
```

---

## 3. チャットとダッシュボード等のUI連動（Shared-State Architecture）

「チャットで指示した結果に応じてダッシュボードのグラフやフィルタが切り替わる」アプリケーションでは、**制御用の軽量ステート**と**描画用の大容量データ**の経路を明確に分ける。

### 原則1: `session.state` には「画面の制御状態（フィルタ条件・選択中ID）」だけを入れる
- ADKのツール内で `tool_context.state["dashboard_view"] = ...` を更新すると、ADKランタイムはそれを `Event.actions.state_delta` としてストリーム送出する。
- FastAPIの `stream_adapter.py` は、テキストのストリームと並行してこの `state_delta` を SSE（AG-UI プロトコルまたは Vercel AI SDK Data Stream の data パート）として Next.js へ送る。
- Next.js 側は `state_delta` を受け取ったらフロントエンドの共有ストア（Zustand 等のフィルタ状態や TanStack Query のクエリキー）を更新する。

### 原則2: グラフ用の生データやML推論の大量レコードは `session.state` やLLM返り値に入れない
- 何千行もの集計結果や予測時系列をツールの return 値や `session.state` に入れると、コンテキストウィンドウを圧迫してレイテンシとコストが悪化する。
- ツールは結果をDB（または一時キャッシュ / ADK `ArtifactService`）に保存し、**「LLMには数行のサマリ統計量」「`tool_context.state` には `dataset_id` または `run_id`」**だけを渡す。
- Next.js のダッシュボードコンポーネントは、`state_delta` で更新された `run_id` を検知して、**通常の高速な REST API（`api/routers/dashboard.py`）から描画用データをフェッチ**する。

### 原則3: ユーザーが画面上で手動変更したフィルタ状態は、次のチャット送信時に同期する
- ユーザーがダッシュボードのUI上で直接フィルタを変えた場合、チャット送信時のリクエストペイロード（またはセッション更新API）で現在の `dashboard_view` を渡し、`runner.run_async()` の実行前に `session.state["dashboard_view"]` を最新化してからエージェントを走らせる。

---

## 4. チャット（対話）と裏側の非同期処理（バックグラウンド）の設計と使い分け

Webアプリ内で「チャット対話」と「裏側で動くAI処理（非同期ジョブ・Webhook・定期バッチ・画面ボタンからのワンショットAI処理）」の両方がある場合、同じエージェントを無理に使い回さず、**入口のエージェント種別を分けて下層の `use_cases/` と `tools/` を共有する**。

| 処理の性質 | 使うべきADKプリミティブ | 入出力と制御の設計 |
| :--- | :--- | :--- |
| **チャット対話**（ユーザーの曖昧な指示の解釈・対話的な深掘り） | `LlmAgent`（＋必要に応じ `sub_agents` や `mode="task"` の委譲） | 自由テキスト入力＋画面コンテキスト。ツール呼び出しと `state_delta` で画面を操作しつつ自然言語で応答する。 |
| **裏側の非同期AI処理**（手順が決まった分析パイプライン・レポート自動生成・イベント駆動処理） | ADK 2.0 `Workflow`（グラフAPI）または `SequentialAgent` / `ParallelAgent` / `LoopAgent` | **進行管理をLLMの判断に任せない**。グラフのEdgeや `SequentialAgent` で手順を決定論的に固定し、推論が必要なノードだけに `input_schema` / `output_schema`（Pydantic）付きの `LlmAgent` を配置する。 |
| **AI判断が不要な定型処理**（単純な集計・MLモデルAPIの定型推論） | **ADKを通さない**（`use_cases/` を直接呼ぶ） | AIエージェントを経由させず、FastAPIのRESTルーターやワーカーから直接 `use_cases/` を呼ぶ。 |

### FastAPI での `Runner` ライフサイクル管理
- `google.adk.runners.Runner` と `SessionService`（本番は `DatabaseSessionService` 等）はステートレスかつ非同期並行実行セーフであるため、リクエストごとに生成せず、FastAPI の `lifespan` でシングルトンとして初期化する。
- チャット用とバックグラウンドワークフロー用でルートエージェントが異なる場合は、`App` / `Runner` をそれぞれのルートエージェントに対して起動時に組み立てておく。

---

## 5. テストと評価（Unit Test と Eval の分離）

AIアプリの品質保証は、決定論的なテストと確率的な評価（Eval）を混ぜない。

1. **`tests/unit/`（決定論的テスト・LLM呼び出しゼロ）**:
   - `<domain_modules>/` と `use_cases/`、および `agents/tools/`（モックの `ToolContext` を渡す）と `api/stream_adapter.py` を `pytest` で高速に検証する。
   - **禁止事項**: LLMの応答文面のキーワード一致などを通常の `pytest` に書かない。
2. **`tests/eval/`（ADK Eval・エージェント振る舞い検証）**:
   - 「ユーザーの指示に対して正しいツールを選び、正しい引数を渡したか（Trajectory評価）」「最終応答が基準を満たすか」は、`google-agents-cli-eval` スキルに従い `agents-cli eval` で検証する。

---

## 6. インタラクティブUI（キャンバス・複数ペイン）とAI編集の不変条件

図面・レイアウトキャンバスや複数ペインの集計表とチャットUIを組み合わせるアプリケーションでは、操作体験の手戻りを防ぐため以下の不変条件を守る。

1. **視点（ズーム倍率・パン位置）の不変維持**:
   - 要素の新規作成・移動・回転・削除・割当解除・API再取得の前後で、キャンバスの表示倍率（`zoom`）とスクロール/パン座標（`pan`）を初期位置へリセットしない。
   - 拡大・縮小は左上原点ではなくマウスカーソル位置を中心に計算し、高倍率ズーム時は線幅・文字縁取りの肥大化を抑え、要素内のラベル文字サイズやグリッド表示間隔を倍率に応じて調整する。
2. **ポインタ操作の非競合と選択・Undoの一貫性**:
   - 余白の左ドラッグ（画面パン）と矩形範囲選択（右クリックメニューや修飾キー併用など）を競合させない。
   - 単体クリックはクリックした1要素のみを選択し（意図せず連結列や島全体を選択しない）、余白クリックまたは `Esc` キーで全選択・ハイライトを完全に解除する。
   - 編集操作には多段階の Undo（`Cmd/Ctrl+Z` および画面ボタン）を備える。
3. **手動変更・AIツール変更の画面全体同期と差分プレビュー承認**:
   - 手動またはAIツールで本数・配置を変更した際、部門合計・予測売上/粗利差分・左ペイン一覧などの派生表示を同一の正本データから即時再計算し、一部ペインだけ古い数値が残る不整合を起こさない（差分0をマイナス色・赤字で表示しない）。
   - 画面上で事業部やDPT等の表示・編集スコープを絞っている場合、そのスコープ条件を `session.state` 経由でAIツールにも引き継ぎ、スコープ外を勝手に書き換えない。
   - AIチャットのレイアウト・計画変更ツールは即座に確定データへ上書きせず、図面・画面上で変更前後の差分プレビュー（承認 / 破棄、承認後はUndo対象）を提示してから反映する。
4. **画面文言（UIマイクロコピー）の自己完結とドメイン表記統一**:
   - 指標名・集計値・軸ラベルには「何の・いつの・何に対する比較か」を省略せずに書く（「集計9週の平均」「±0%基準」「実質効果」のように対象が不明な省略をせず、「改装後9週間の週平均荒利益」「類似店平均との差（0%＝類似店並み）」のように画面単体で意味が確定する言葉にする）。
   - 「決めること：」「分析の段階：」「深掘り指標」のような書き手都合のメタ見出しや曖昧語を置かず、業務担当者がそのまま判断に使える具体的な表現（「個別分析」や意思決定の目的文そのもの）にする。
   - 対象組織の正式表記（例：カインズ向け画面では「粗利」ではなく「荒利益」、相対呼称「使う人」「ユーザー」ではなく具体的な担当役割名）に揃える。

---

## 設計・レビュー時のチェックリスト

設計提案やコードレビューを行う際は、以下の観点で反証・確認する。

- [ ] **ADK依存の隔離**: `use_cases/` や専門モジュール、DBリポジトリが `google.adk` や `ToolContext` を import していないか？
- [ ] **ツールの薄さ**: `agents/tools/` の関数内にSQLや外部API通信、複雑な条件分岐が直書きされておらず、`use_cases/` の呼び出しに委譲されているか？
- [ ] **DIとスキーマ汚染の防止**: ツール関数の引数にDBセッション等のインフラオブジェクトが露出しておらず、ファクトリ注入＋`ToolContext` 経由になっているか？
- [ ] **大容量データの分離**: グラフ描画用の生データ配列や巨大なML推論結果を `session.state` やツールの返り値に直接詰め込まず、DB/Artifact保存＋参照IDの `state_delta` 同期＋REST API取得に分離しているか？
- [ ] **決定論的フローの固定**: 裏側の非同期処理や手順が決まったパイプラインを、単一の `LlmAgent` のプロンプト指示だけで制御しようとせず、`Workflow` や `SequentialAgent` 等で構造化しているか？
- [ ] **視点・選択・AI差分の整合**: キャンバス編集時にズーム/パンがリセットされないか？パンと範囲選択が競合していないか？画面の絞り込みスコープがAIツールに伝播し、破壊的な変更は差分プレビュー承認＋Undo対応になっているか？
