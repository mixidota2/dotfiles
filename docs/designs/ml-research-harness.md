# ML Research Harness — Design Doc

> Status: Implementation-ready Draft（設計判断は合意済み、コード・live統合は未検証）  
> Updated: 2026-11-17  
> Audience: 別のGitHubアカウント・環境で実装を引き継ぐ人間／コーディングエージェント  
> Scope: 汎用ML研究開発ハーネス。実際の業務データ、資格情報、秘密のGCPリソースは含めない。

## 0. 決定事項（まず読む）

**C1：Skill主導＋実験ツール**を採用する。Codex等のエージェントが研究上の判断、データ分析、コード実装、次の実験選択を行い、Pythonは実験の記録・実行・評価・再現性を保証する。最初のControl Planeはローカル。重い学習だけをリモートに送れるようにする。

| 設計要素 | 採用した方針 |
| --- | --- |
| 研究制御 | Skill主導。初期にはPython製の研究Supervisorを作らない |
| 配布 | 1リポジトリ内にSkill／プラグイン設定と独立実行可能なPython CLI・ライブラリを同梱 |
| 実験管理 | ローカルSQLiteを正本とし、GitとArtifact Storageに不変なコード・結果を保存 |
| タスク・知見管理 | 既存のtk/tasukuraを維持。実験DBと二重管理しない |
| データ取得 | 認可されたBigQuery等のデータ範囲でSQLとデータ加工は自由 |
| 評価 | 評価用SQL、データSnapshot、分割、ラベル定義、指標、EvaluatorをProtocolとして固定 |
| 実行 | 候補内部のMLパイプラインは自由。評価用Artifactの契約だけ共通化 |
| 階層 | Approach → Candidate → Run → Attempt。SearchCampaignは探索活動をまとめる別軸 |
| ハイパーパラメータ | Loss選択、特徴量フラグ、seedを含む具体的な解決済みパラメータはRunへ |
| 研究ブランチ | 構造的に異なるApproach・並行競合する変更に限りworktreeを分岐 |
| Accept | Verified Runを有望な解法として採用。複数並立できる |
| Promote | Accepted Runをmainへ直接mergeせず、整えて再評価したコードをPRで統合 |
| AlphaEvolve | 初期から専用Adapterを作成。公式クライアントと共通Evaluatorを接続 |
| リモート実行 | ジョブ投入・状態取得・回収の契約を初期から定義。Local Backendを先行 |

この文書のコマンド名・データ型・ディレクトリは**実装提案**であり、現存するCLIやAPIではない。未決事項は第13節へ明示する。

## 1. 目的と非目標

### 目的

- エージェントがデータ取得SQL、EDA、データ品質・バイアス調査、学習データ作成、分割設計、ベースライン実装からモデル改善まで実施する。
- LightGBM、NN、時系列、二段階モデル、アンサンブルなど、全く異なる解法を自由に検討する。
- 任意の評価・分析形式で成果物を出力する。数値の正本は共通のEvaluatorで計算する。
- 成功・失敗・研究上の示唆を残し、最終的に整理されたコード、再現可能な実験結果、試行錯誤のレポートを出力する。
- 大きなアプローチを並行に長期間育てても、mainの品質と単純さを損なわない。
- 最初はローカル主体で運用し、GPUなどの重い処理をクラウドへ委譲できる。
- 動作するベースラインをAlphaEvolveに渡し、生成された候補も同じRegistryで扱える。

### 初期の非目標

- Pythonから汎用コーディングエージェント全体を制御する独自フレームワーク
- 研究フェーズ全体を固定Python状態機械にすること
- Remote Supervisorによる24/7自律研究、クラウドDB、独自進化的探索アルゴリズム
- すべてのMLタスクに同じprepare/train/predictメソッドを強制すること
- 検証スコアの向上だけで本番でのビジネス効果が証明されたと扱うこと

## 2. 根拠と設計原則

- [CSIRO Image2Biomassの5位解法](https://github.com/Chihiro-n/csiro-biomass-agentic-solution) はClaude Code/Codexによる実装、EXPとchild-expの区別、実験サマリ・失敗の記録を公開している。これを出発点とするが、汚くなりやすい探索コードはmainへ直接入れず、worktreeとPromotionで整理する。
- [AIDE](https://github.com/wecoai/aideml) の探索木と [MLEvolve](https://github.com/InternScience/MLEvolve) の複数系列・実験メモリを参考とする。ただし、彼らの競技成績は本ハーネスの優位性を実証しない。
- 実装の責務分割は、このリポジトリの [design-python-code Skill](../../skills-local/.codex/skills/design-python-code/SKILL.md) に従う。名称やパターンから分割せず、状態・不変条件・変更理由から最小構造を選ぶ。
- 新しい研究Skillは [create-validated-skill](../../skills-local/.codex/skills/create-validated-skill/SKILL.md) を用い、代表タスクの成果、Skillなしとの比較、失敗条件を評価する。
- 設計提案の根拠と、実装・統合テストで確認した事実を混同しない。

## 3. 全体アーキテクチャと責務

~~~mermaid
flowchart TD
  Agent[Codex / Claude Code] --> Skill[ML Research Skill]
  Skill --> CLI[mlh CLI]
  Skill --> tk[tk CLI]
  CLI --> Core[Experiment Core]
  Core --> DB[(Local SQLite)]
  Core --> Jobs[Job Backend]
  Jobs --> Local[Local]
  Jobs -. optional .-> Remote[Vertex AI / Cloud Run Jobs]
  Local --> Store[Artifacts]
  Remote --> Store
  Core --> Eval[Protected Evaluator]
  Eval --> DB
  CLI --> AEAdapter[AlphaEvolve Adapter]
  AEAdapter <--> AE[Google Cloud AlphaEvolve]
  AEAdapter --> Jobs
~~~

この図は**呼び出し／データフロー**であり、Pythonのimport依存ではない。

| Owner | 責務 | 非責務 |
| --- | --- | --- |
| Agent + Skill | EDA、仮説、コード変更、研究方針、結果解釈、レポート | Registryの整合性、保護された評価処理 |
| ML Project | SQL、前処理、モデル、推論、固有のArtifact生成 | Run/Attempt状態、共通評価の正本 |
| Experiment Core | オブジェクトの意味・状態遷移・比較制約・実験記録 | MLモデルのアルゴリズムやGCP SDK |
| Job Backend | ジョブ投入・状態照会・中止・成果物取得・障害復旧 | モデル選択、採否判断 |
| Evaluator | 固定Protocolを適用したスコア・妥当性検査 | Candidateの学習と研究方針 |
| tk | 研究の進捗、decision/finding/blocker/question/hypothesis | 全Runの数値・ジョブログ |
| AlphaEvolve Adapter | Program/候補、scores/結果、外部ID、Controllerとの対応 | 進化アルゴリズムの自作 |

状態の正本は、Git（コード・branch）、Registry（実験の意味・状態・指標）、Artifact Storage（不変コードBundle・データ・モデル・予測）、tk（研究上の判断）、AlphaEvolve Program DB（サービス上のProgram）に分ける。

**Python依存方向**：CLI/adapterがCoreの公開操作を呼ぶ。CoreはクラウドSDK・tk CLIの具体型・例外を知らない。設定、認証、具体Backendの組立てはCLI近くのcomposition rootに置く。

## 4. Data Access PolicyとEvaluation Protocol

### Data Access Policy

プロジェクト設定に「参照してよいBigQueryのproject/dataset/view・利用するbilling project・location・費用上限」を記述する。クエリ自体は自由に作成・変更できる。

- 設定だけで権限を保護したことにしない。**読み取り専用サービスアカウントとIAM**で実際のデータ境界を強制する。
- dry run、maximum bytes billed、タイムアウト等の費用制限を行う。
- 学習SQLのJOIN、抽出期間、集約粒度、サンプリング、欠品補正などはエージェントが変更できる。
- SQL本文・パラメータ・参照元・取得日時・スキーマと**データ内容を識別するSnapshot/Digest**を記録する。
- SQLのハッシュが同じでも、参照データが更新されたら同じSnapshotとはみなさない。
- 学習データを作り直すだけなら新DatasetSnapshot + Run。モデル側のSQL生成**実装**を変えたなら新Candidateにもなる。

### 共通Evaluation Protocol

**評価用のSQLと評価セットも共通化する**。Protocolの識別対象：

1. 評価対象母集団、観測単位、予測キー
2. 正解ラベル定義、評価SQLのバージョン
3. 不変の評価Snapshot（Parquet、BQ Snapshotなど）
4. 時系列／グループ分割、利用可能時点、CV Fold
5. 指標・集約方法・最大化/最小化方向
6. Evaluatorのバージョンと不正出力の扱い

変更したら新Protocol IDを発行し、旧Protocolのスコアとは直接比較しない。新評価系で比較したければ共通ベースラインを再評価する。

学習データはCandidate/Runごとに変更できる。評価期間の説明変数は予測に必要なら使えるが、**評価ラベルおよび最終Holdoutは候補側から隔離**する。研究用Validationを何度も使うことによる過剰適合、欠品に伴うラベルの打ち切り、未来情報リークなどは明示的な検査対象。

## 5. 実行契約：内部自由、評価境界のみ共通

Harnessはprepare/train/predictを固定APIにしない。Candidateに指定した任意コマンドを実行し、必要な成果物を出力してもらう。

- Candidateのコード参照はGit commitまたはContent Digest付き不変コードBundle。
- Run Specは解決済み設定、学習Snapshot、評価Protocol、seed、実行コマンド、環境・リソースを含む。
- ML Projectは所定の出力先へArtifact Manifestを保存する。
- Protocolが必要なArtifactのタイプとスキーマを宣言。表形式予測なら予測キーと予測値、ランキングならクエリごとのスコア、最適化なら目的値・制約充足結果など。すべてを予測Parquetに固定しない。
- Harnessが形式・キーの重複／欠損・対象の網羅性を確認し、Protected Evaluatorが採否に使う指標を算出する。
- ML Project自身が出力したメトリクスは参考値とし、Verified Scoreの正本にはしない。

### Run Spec（例・未確定の提案Schema）

~~~yaml
candidate_id: cand_lgbm_007
request_id: client-request-123
campaign_id: campaign_hpo_01
resolved_config:
  learning_rate: 0.05
  num_leaves: 63
  objective: regression_l1
  early_stopping_rounds: 50
  features: [price_lag, stock]
seed: 42
train_dataset_id: train_v4
evaluation_protocol_id: temporal_cv_v2
execution:
  backend: local
  command: ["uv", "run", "python", "-m", "demand_forecast.train", "--config", "run.yaml"]
  timeout_seconds: 3600
~~~

実行前に、**デフォルトを含む全ハイパーパラメータと設定値を解決して不変保存**する。コード変更がない限りHPO値の違いはRunの違いとする。

## 6. Registryのデータモデル

| Entity | 定義 | IDが変わる条件 |
| --- | --- | --- |
| Approach | アルゴリズム体系、学習・推論フロー、学習問題の構成方式が一貫する研究系列 | 解法体系または主要な処理構造が変わる |
| Candidate | コードと設定スキーマの不変Snapshot | 実装コード・設定スキーマ等を変更 |
| Run | Candidateに具体的ハイパラ・学習Snapshot・seed・Protocolを与えた**一つの論理評価** | 確定パラメータ、データ、seed、評価条件等が変わる |
| Attempt | 一つのRunを実行した物理的な試行 | Retry、失敗回復などの再実行 |
| SearchCampaign | Codex、Optuna、AlphaEvolve等の探索目標・予算・方式を束ねる | 新たな探索活動開始 |
| Acceptance | Verified Runを採用した理由と適用範囲 | 採否・判断根拠の登録 |
| Promotion | Accepted Run由来のコードをmainに統合する活動 | 統合作業開始 |
| DatasetSnapshot | 物理データ内容と取得条件の不変参照 | 取得結果が変わる |
| EvaluationProtocol | 共通評価条件の不変参照 | 比較条件が変わる |

- **Approach → Candidate → Run → Attempt**が基本系譜。SearchCampaignとAcceptanceは別の参照関係であり、無理に階層へ入れない。
- Candidateはコード参照種別をGitかArtifactにできる。AlphaEvolveの全Programへcommitやworktreeを作成しない。
- 1 Run = 5 Fold CVなど完結した一評価。Fold値はRunの結果。Optunaの各Trialは基本別Run。
- 同じRunの障害復旧は別Attempt。意図的に同条件で再現性を測る実験は別Runにし、関連元を記録する。
- Runは作成後に設定・データ・Protocolを変更しない。verified後の結果も上書きしない。
- AcceptはCandidateではなく**Run**。ハイパラまで採用対象に含まれる。異なるRunを複数Acceptedにできる。

想定SQLiteテーブル：approaches、research_branches、candidates、runs、attempts、campaigns、acceptances、promotions、datasets、protocols。確定設定・metrics等はJSON列でもよい。重複受付の排除にはRun仕様Digestと**リクエストの冪等キー**を区別し、同設定を意図して別Runとして評価する操作も可能にする。

### 状態

- Approach：active / paused / archived
- Candidate：不変Snapshotであり、採否を状態に混ぜない
- Run：created → running → verifying → verified / invalid / failed。外部実行との同期が失われたらunknown
- Attempt：submitted / running / succeeded / failed / timed_out / unknown
- Acceptance：対象Run、理由、スコープ、採用／撤回履歴
- Promotion：prepared / verifying / ready / merged / abandoned

状態の書換えと許可遷移はCoreのowner。クラウドジョブ・Git・tk等の直接書込み経路で不正状態を作れないようにする。

## 7. Git / worktree / Accept / Promote

### 新Approachとworktreeを作る判定

**新Approach**は次のいずれかを満たす場合：

1. 学習アルゴリズム体系が違う（LightGBM→Transformer、ALS→Two-Towerなど）
2. 学習・推論ステージの主要構造が変わる（単一→二段階、直接→再帰予測、スタッキング）
3. 学習問題の構成方法が本質的に変わる（単純売上回帰→別段階で潜在需要を推定等）

探索パラメータ数が増えるだけでは新Approachにしない。Lossの選択やハイパラはRun。**新たなLoss・特徴量実装**は同Approachの新Candidate。SQLパラメータ変更は通常新DatasetSnapshotとRun。

**worktreeはApproachと1対1に固定しない。** 新Approachなら原則別worktree。同Approachでも、相反するコード変更を並行かつ継続的に育てる場合のみ派生worktree。単発の大規模変更を試すなら一時spike worktreeも許容。不要になればworktreeは削除できるが、必要なCandidateのコード参照とArtifactは保持する。

| 例 | Approach | Candidate | Run | worktree |
| --- | --- | --- | --- | --- |
| 学習率やLoss設定を変更 | 維持 | 維持 | 新規 | 不要 |
| 新特徴量・カスタムLossを実装 | 維持 | 新規 | 新規 | 原則不要 |
| 学習SQLの期間や対象を設定変更 | 維持 | 維持 | 新規 | 不要 |
| LightGBM→CatBoost/Transformer | 新規 | 新規 | 新規 | 新規 |
| 単一予測→二段階予測 | 新規 | 新規 | 新規 | 新規 |
| 同一Approachの競合する実装を並行育成 | 維持 | 別系列 | 別系列 | 派生 |
| AlphaEvolveが多数Program生成 | 通常維持 | 各ProgramをCandidate化 | 各評価Run | Programごとには不要 |

Candidateのparent_candidate_idは研究上の親子関係。Git commitの親子関係と完全同一である必要はない。研究branchは長期保持してよく、Accepted Runがbranch HEADである必要もない。

### Verified / Accepted / Promoted

- **Verified**：Evaluatorが固定Protocolで結果を確認した。
- **Accepted**：研究系列の有望なRunとして採用した。複数同時可。別系列を終了させない。
- **Promoted**：mainからintegration branchを作り、採用元のコードと**採用したRun設定**を必要な形に整理、設計Skillでリファクタリング、再評価、テスト、PR経由でmergeした。

Promotion後のコードは元のCandidateと異なる可能性があるため、新Candidate＋新Runとして再評価し、元Accepted Runとの比較を記録する。mainは最高スコアの実験コードを置く場ではなく、保守可能な検証済み実装を置く場とする。

## 8. ローカルControl PlaneとRemote Job Backend

初期は**ローカルSQLite＋ローカルJob Backend**。モデル・予測・データ等の大きなArtifactはローカルFilesystemを基本とし、必要に応じてGCS URIを使う。リモートの学習プロセスはRegistryへ直接書かない。

具体的なLocal／Vertex AI等の交換が想定されているので、次の最小契約にする。

~~~python
class JobBackend(Protocol):
    def submit(self, spec: JobSpec) -> JobHandle: ...
    def status(self, handle: JobHandle) -> JobStatus: ...
    def cancel(self, handle: JobHandle) -> None: ...
    def collect(self, handle: JobHandle) -> ArtifactManifest: ...
~~~

JobSpecにはRun ID、Candidate・コード・設定・Datasetの不変参照、環境、リソース、timeout、入出力URI、再送識別子を含む。JobHandleには外部job IDとbackendを保持する。Local実装には標準ライブラリやInvoke等を適切に使えるが、公開APIは特定ライブラリに固定しない。

Remote Jobの運用：
1. Run/Attemptを先に永続登録してから投入
2. 外部job IDをRegistryに記録し、cloud jobのラベル等からも再発見可能にする
3. PCを停止してもremote training自体は継続できる
4. 再開したらsyncで状態を照合し、ArtifactをcollectしてEvaluatorで確認
5. 結果不明の実行を安易に再送せず、重複ジョブを防ぐ
6. 実行環境（GPU、ライブラリ等）をAttemptにも記録。別環境での独立再現評価なら新Runとする

初期はLocal Backend必須。Remote Backendは最初の実運用先に応じてVertex AI Custom JobsまたはCloud Run Jobsを追加する。**Remote Jobを持つことと、研究エージェントをクラウドで常時回すC2化は別**。

## 9. AlphaEvolve Adapter（初期実装対象）

公式AlphaEvolveは、管理された候補生成・Program DB・探索サービスと、利用者側のEvaluator/Controllerからなる。**単一の非同期学習ジョブを投げて完成結果を待つだけのサービスではない**。公式のPythonクライアントが提供するController（run_controller_loop等）を利用する。[公式リポジトリ](https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud)、[アーキテクチャ](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/architecture-and-workflows)。

初期Adapterの責務：
1. 実行可能なAccepted RunからSeed Programを作り、進化対象コード範囲を用意する。
2. SearchCampaignを作り、AlphaEvolve experiment ID・seed・評価Protocolを関連付ける。
3. Programを受け取って不変コードBundleとして保存し、Candidate IDを付ける。
4. 共通のJob Backend/Protected Evaluatorで評価し、Run/Attemptを保存する。
5. 指標の最大化方向変換、AlphaEvolve固有のscores/insights作成・送信を行う。
6. 外部Program ID・親子関係・エラーを保存し、有望候補をGit研究branchに取り込めるようにする。

| AlphaEvolve側 | Harness側 |
| --- | --- |
| Experiment | SearchCampaign |
| Program | Candidate（artifact source） |
| Evaluation | Run |
| 評価ワーカー実行試行 | Attempt |
| Scores / insights | Verified Metrics / Diagnostics |

AlphaEvolveがパラメータをコードに書き込んだ場合は新Candidate。独立設定だけ変えるHPOの場合は別Run。ProgramごとにGit commit・worktreeを要求しない。外部サービスのProgram DBとローカルRegistryは外部IDとContent Digestで関連付ける。

初期はローカルController。PC停止中も**探索の連続評価まで**行いたければControllerもクラウドに移設する必要がある。生成はクラウド、評価場所はローカルでもリモートでもよい。[公式Evaluatorパターン](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/evaluator-patterns)。

公式Skillが検証する範囲と、MLコード全体を自由に作る一般エージェントの範囲を混同しない。必要なGoogle Cloud契約・IAMが使えない時点では、Mock/tiny evaluatorを通しても**live統合未検証**と明示する。APIの変更があればこのAdapterだけを修正し、Coreへサービス固有の型を漏らさない。

## 10. tkとエージェント向けContext

- tk：研究タスク、長期の決定・知見・未解決問題。既存のprogress_logs→typed records昇格ルールを維持。
- Registry：全Candidate/Run/Attempt、スコア、失敗、派生、Artifact参照。
- Context Builderは、プロジェクト目標、固定Protocol、tkの有効な記録、Verified/Accepted Run、直近の関連実験、次アクション、残り予算をコンパクトに集める。
- 全ログや全実験コードを毎回コンテキストに入れない。実験IDから元Artifact、tk Record IDから元ログへドリルダウンする。
- 全RunをtkのタスクやRecordにしない。検証済みの重要な知見だけを進捗ログからRecordへ昇格させる。
- エージェントの自己申告よりRegistryの検証済み状態を優先する。

## 11. 配布と実装treeの暫定案

Skill・プラグイン設定とPython CLIを**一つの実装リポジトリ**でバージョン管理する。各MLプロジェクトへハーネス一式をコピーするテンプレート方式は主軸にしない。新規プロジェクトの初期化用テンプレートは後から追加できる。

~~~text
ml-research/                        # 将来作成する実装リポジトリ（まだ存在しない）
├── pyproject.toml
├── AGENTS.md
├── skills/ml-research/
│   ├── SKILL.md
│   └── references/
│       ├── research.md
│       ├── experimentation.md
│       ├── branching.md
│       ├── promotion.md
│       └── reporting.md
├── src/mlh/
│   ├── cli.py
│   ├── experiments/              # Candidate/Run/Acceptの意味
│   ├── execution/                # Runner、JobBackend、Attempt
│   ├── evaluation/               # EvaluationProtocolと共通評価
│   ├── data/                     # DatasetSnapshotと権限境界
│   ├── git/                      # research branch / worktree
│   ├── alphaevolve/              # 外部ProgramのAdapter
│   └── context.py                # tkとRegistryの選択的参照
├── tests/
├── examples/toy_forecasting/
└── docs/design.md
~~~

このtreeは固定テンプレートではない。最初はflat moduleでよく、状態・変更理由・依存境界が実際に独立した箇所だけsubpackageに昇格する。既存のML Projectのディレクトリ構造は強制的に変更させない。Pythonプロジェクトがuvを使っているならuvを維持する。

想定CLI（全て**提案名／未実装**）：

| 操作 | 目的 |
| --- | --- |
| mlh init | Project導入 |
| mlh approach create / branch create | 新研究系列・研究worktree |
| mlh candidate capture | 不変コードSnapshot登録 |
| mlh run submit / status / sync / wait / collect | 実行と状態照合 |
| mlh compare | 同じProtocolの実験比較 |
| mlh accept | Verified Runを理由付きで採用 |
| mlh promote prepare | mainから統合branchを準備 |
| mlh context | 次セッション向けContext |
| mlh alphaevolve start / status / collect | 公式Controllerの利用と結果取り込み |

## 12. 実装順序と受け入れテスト

### Phase 0：実行契約のPoC
Toy forecastingでCandidate → 2種類のHPO Run → Attempt → 固定Evaluator → Registry → 比較結果をローカル完走。再現不能なCandidate、Protocol違反、重複キーを拒否する。

### Phase 1：Kaggle型の研究運用
Skill、tk、データアクセスの設定、Approachとworktree、複数Accepted Run、Promotion、レポート生成を実装。新SkillはSkillあり・なしの比較を行う。

### Phase 2：AlphaEvolve Adapter（初期版の要件）
Toy Seedと公式Controllerを使い、Program → Candidate → Run → scores/insights → 有望候補取り込みを確認。Google Cloud権限が使えない場合はMock PassとLive未検証を区別する。

### Phase 3：最初のRemote Job Backend
実際の要件に応じてVertex AI Custom Jobs等を一つ実装。投入直後のローカル異常終了、リモート完了後のローカル停止、二重送信、成果物欠落をテストする。

**受け入れシナリオ**：

1. 同じCandidateのHPO値を変えてもRunだけ増え、同じCandidateのコードは増殖しない。
2. 新しいカスタムLossは同Approachの新Candidate、Transformerへの変更は新Approachとなる。
3. 2つのworktreeを並行に育て、親Candidateの系譜とRun結果を独立保持できる。
4. 複数RunをAcceptしても別系列を終了させない。
5. 同一リクエストの再送は重複ジョブを生まず、意図的な再現実験は別Runになる。
6. Protocol違反、評価データリーク、予測キー欠落等を無条件でverified扱いしない。
7. Promotionでは整理後コードを新Candidateとして同条件で再評価し、採用元Runとの対応を残す。
8. AlphaEvolve Programを不変コードBundleとして保存し、共通EvaluatorのRunを返せる。
9. リモートジョブの状態はPC停止後もsyncで再発見・検証できる。
10. 完成コード、任意形式の評価Artifacts、失敗含む研究レポートが納品できる。

## 13. 残る未決事項とリスク

以下は設計を先送りしたことを明記し、**実装済みと扱わない**。

1. Artifact Manifestのスキーマ：表形式予測以外への一般化、キー・欠損・重複判定
2. Evaluation Protocolの凍結操作、最終Holdoutの隔離方法、承認権限
3. Snapshotの物理形式・retention・GCSコスト・PIIの取り扱い
4. Remote Jobの冪等性、timeout・partial success・orphan Job再発見の実装
5. AlphaEvolveのSDK/API、契約・IAM、live Controllerと評価コードの連携確認
6. Codex/Claude Codeプラグイン配布仕様と共通Skillの移植性
7. BigQueryの権限と実行費用上限、実際のデータドメインの妥当性
8. 多量のCandidate Bundleをいつ削除してよいかのGC・参照保持

**設計時点で採用しない代替案**：
- Python主体の研究Supervisor：既存エージェントの研究能力と役割が重複しやすい。無人常時実行が必要になればC2として追加。
- 研究コードを1本のmainに積み重ねる：コード肥大化と元実験の追跡困難を招く。
- 1実験1worktree：維持コストが大きい。
- 固定train/predict関数API：大きく異なる手法への拡張を阻害する。
- クラウドDB：最初は単一主要writerのローカルSQLiteで十分。複数writerの必要性が出たら再検討。
- テンプレートリポジトリ主軸：既存Projectへ更新を反映しにくい。

## 14. 設計セルフレビュー（実装前の暫定）

[design-validation](../../skills-local/.codex/skills/design-python-code/references/design-validation.md) の10観点。**Passは文書上のシナリオに対する評価であり、実装済みを意味しない。**

| 観点 | 判定 | 理由／未確認 |
| --- | --- | --- |
| Coverage | Pass（文書範囲） | 研究開始から完成物までownerを配置 |
| Ownership | Pass（提案上） | Git、Registry、tk、Artifacts、AEの正本を分離 |
| Cohesion | Pass（提案上） | 研究判断、実行、評価、外部接続を分離 |
| Dependency | Revise（未実装） | 実際のimport・環境初期化・SDK漏出は未検証 |
| Abstraction necessity | Pass（提案上） | 実在するLocal/Remote、AE接続のみを先取り |
| Orchestration | Revise（未実装） | cloud投入のpartial success、transaction、重複回避を要確認 |
| Changeability | Pass（設計シナリオ） | HPO、新手法、Dataset、Remote、Promotionを分離 |
| Cognitive load | Revise（未実装） | CLIからの実際のcall traceで確認 |
| Evidence | Revise（未確認） | 公開事例と明示要件は確認。SDK・実行境界は実証が必要 |
| Simplicity | Pass（提案上） | 研究Supervisor、クラウドDB、複雑なPlugin Registryを保留 |

**境界の反証（Merge/Delete）**：
- Core状態とSQLite保存を完全統合すると、不変条件の責務と保存方式の変更が結び付く。境界は維持。ただし初期は小さなモジュールでよい。
- JobBackendを削除すると、Local→Vertex移行がCLIやRun管理へ波及。残す。
- AlphaEvolve AdapterをCoreへ統合すると、外部API型・Program ID・スコア規則がCoreへ漏れる。残す。
- Contextに別の永続DBを作るとtk/Registryが重複する。作らない。
- Python製Research Orchestratorを削除しても現在の目的は満たせる。初期には作らない。

**entrypoint trace（設計上）**：
- mlh run submit → CoreがRun登録 → JobBackend → ML Project → Artifact → Evaluator → Registry
- mlh alphaevolve start → AE Adapter → 公式Controller ↔ AE managed service → 共通JobBackend/Evaluator → Registry → scores返却
- Skillによる再開 → tk show ＋ mlh context → 研究の次の判断
- Promotion → Accepted Run → integration branch → 新Candidate・再評価 → PR／merge

未解消のReviseは実装・統合試験時に必ず検証する。

## 15. 仕事用アカウントへの引き継ぎ

この文書は**提案実装の仕様と判断理由をまとめたもの**。このdotfilesリポジトリにML Harnessの本体は実装しない。仕事用アカウントで独立した実装リポジトリを作り、この文書を仕様の出発点とする。

最初のタスクは **Phase 0のtoy forecastingで、同じCandidateの2つのハイパーパラメータRunと固定Evaluatorを再現できること**。いきなり全ディレクトリや抽象クラスを作らず、実際の変更・失敗・importを調べてdesign-python-codeに沿って分割する。

### 一次資料／参照

- Google Cloud AlphaEvolve overview: https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/overview
- AlphaEvolve architecture: https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/architecture-and-workflows
- AlphaEvolve evaluator patterns: https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/evaluator-patterns
- Official AlphaEvolve client: https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud
- Git worktree: https://git-scm.com/docs/git-worktree
- Kaggle CSIRO agentic solution: https://github.com/Chihiro-n/csiro-biomass-agentic-solution
- AIDE: https://github.com/wecoai/aideml
- MLEvolve: https://github.com/InternScience/MLEvolve
- tasukura/tk: https://github.com/mixidota2/tasukura
