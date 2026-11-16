# ML Research Harness 設計書

| 項目 | 内容 |
| --- | --- |
| ステータス | Draft：基本設計を提案済み。実装と外部サービスへの接続は未検証 |
| 更新日 | 2026-11-17 |
| 想定読者 | 機械学習エンジニア、実装を担当するコーディングエージェント |
| 文書の目的 | 新しい開発環境で実装を開始できるよう、要件・構造・判断理由・検証方法を定義する |
| 実装先 | 本文書とは別のML Research Harnessリポジトリ。名称とURLは未定 |

## 1. 概要

ML Research Harnessは、コーディングエージェントに機械学習モデルの研究開発を任せるための仕組みである。データの調査と取得、学習データの作成、評価方法の設計、ベースラインの実装、改善実験、結果の分析、完成コードと研究レポートの作成までを扱う。

中心となるのは、CodexやClaude Codeなどの既存のコーディングエージェントである。エージェントはSkillに従って研究上の判断とコード変更を行う。一方、Python製の実験ツールは実行条件、評価結果、コードとデータのバージョン、障害時の状態を保存する。

初期構成はローカルで動作する。必要になれば学習をVertex AIなどに送ることができる。ベースライン作成後の探索には、Google CloudのAlphaEvolveも使用する。

この設計の要点は次のとおり。

- 学習や推論の実装方法は固定せず、評価に必要な入力・出力だけを共通化する。
- 大きく異なる解法はGit worktreeで並行して研究する。実験コードをそのままmainへ統合しない。
- ハイパーパラメータ、使用データ、評価条件を保存し、結果を再現・比較できるようにする。
- 研究上の知見は既存のタスク管理ツールtkに残し、個々の実験結果は専用のRegistryで扱う。
- AlphaEvolveへの接続は専用Adapterに閉じ込める。一般のMLコードをAlphaEvolve独自の形式に合わせない。

この文書に記載するコマンド、設定、ディレクトリ構成は実装予定の案であり、すでに動作するソフトウェアを説明したものではない。

## 2. 背景と解決したい問題

通常のコーディングエージェントでも、学習コードを作成して評価結果を読み、次の改善案を提案できる。しかし、長期間のML研究では次の問題が生じる。

まず、実験を繰り返すうちに、どのコード・ハイパーパラメータ・学習データで得たスコアなのか分からなくなる。評価方法を途中で変えた場合には、そもそも実験同士の比較が成立しないこともある。

また、実験用の分岐や暫定処理を既存コードに足し続けると、最良モデルの再利用や本番導入が難しくなる。反対に、試行ごとにコードを整理していては、探索の速度が下がる。

データの問題も重要である。例えば小売の需要予測では、欠品日に記録された販売数量が潜在需要を表していない可能性がある。エージェントが特徴量やモデルだけでなく、データの抽出条件、目的変数、分割方法を見直せる必要がある。ただし、評価セットまで自由に変更すると、改善の判断に使う基準が一定でなくなる。

AlphaEvolveは、動作するプログラムと評価器を前提に、コードの候補を進化的に探索する。ベースラインがない段階の調査・実装まで一貫して任せるためには、別の仕組みが必要になる。

以上から、研究の自由度を保ちながら、実験の比較可能性と完成コードの品質を維持する共通基盤を作る。

### 2.1 想定する利用例

例として、BigQueryに保存された売上・在庫・商品情報から、店舗×SKUの28日需要予測モデルを作る。

1. エージェントが許可されたBigQueryデータセットを調査し、欠品や販売数量の定義を確認する。
2. エージェントが時系列の検証方法を設計し、評価用データと評価指標を固定する。
3. LightGBMでベースラインを作り、特徴量、Loss、学習率を変更して複数回評価する。
4. 別の研究ブランチでTransformerを試し、LightGBMの研究も並行して継続する。
5. 有望な実験を採用し、必要な実装だけを整理してmainへ統合する。
6. 完成したベースラインをAlphaEvolveへ渡し、固定した評価指標で追加探索する。
7. エージェントが最終コード、実験比較表、試した仮説と失敗の理由をまとめたレポートを出力する。

この例に登場するデータやスコアは説明用であり、実際の業務環境を示すものではない。画像分類や推薦など、別のMLタスクも対象にする。

## 3. 要件

### 3.1 機能要件

| ID | 要件 | 完了を確認する方法 |
| --- | --- | --- |
| F1 | データの調査・取得SQL・加工方法をエージェントが変更できる | 許可されたBigQuery範囲で2種類の学習データを生成できる |
| F2 | データセット・分割・ラベル・評価指標を定義して固定できる | 同一Protocolで複数モデルを評価し、変更を検出できる |
| F3 | 任意の学習・推論方式を試せる | LightGBMと構造の異なるモデルを同じ仕組みで評価できる |
| F4 | ハイパーパラメータとコード変更を区別して記録できる | 同一コードで異なるパラメータの実験を比較できる |
| F5 | 複数の解法系列を独立して育てられる | worktreeを分けて並行実験し、履歴を追える |
| F6 | 検証済みの実験を複数採用し、後からmainへ統合できる | 採用元と統合後の再評価結果を追跡できる |
| F7 | 途中終了や再試行があっても実行状態を確認できる | 同一ジョブの二重実行を避け、状態を復旧できる |
| F8 | AlphaEvolveの候補と評価結果を共通Registryへ取り込める | toyプログラムを使って往復を検証できる |
| F9 | 評価結果・完成コード・研究レポートを生成できる | 数値をRegistryから再取得し、成果物を再生成できる |

### 3.2 品質・運用要件

- ローカルの単一開発者・単一主要writerを初期条件とし、常時稼働するサーバーは要求しない。
- モデルや学習環境の依存関係をHarness本体へ取り込まない。既存MLプロジェクトの構造を強制的に変更させない。
- 学習用SQL、学習Snapshot、モデルコード、依存環境、評価Protocolを結果に関連付ける。
- 評価用の正解ラベルは、必要な範囲で候補コードからアクセスできないようにする。
- 時間・計算資源・BigQueryの処理量・外部サービス費用に上限を設定できるようにする。
- 研究上の知見や失敗を消さずに保存する。次のエージェントセッションには必要な部分のみ渡す。
- 外部サービスのSDKや認証方式が変わっても、実験データモデルの変更を最小限に抑える。

### 3.3 初期対象外

汎用マルチエージェント基盤、独自の進化的探索アルゴリズム、クラウドDB、複数開発者による同時書き込み、エージェントの24時間無人再起動は初期対象外とする。後述するリモート学習の拡張は含めるが、それだけで研究エージェントが無人で動き続けるわけではない。

## 4. 採用する全体構成

研究の進め方はSkillに記述し、実験の正しさを確認できる処理はPythonで実装する。この方式を以下では「Skill主導型」と呼ぶ。

~~~mermaid
flowchart TD
  User[利用者] --> Agent[Codex / Claude Code]
  Agent --> Skill[ML Research Skill]
  Skill --> CLI[mlh CLI / Python library]
  Skill --> TK[tk: タスクと研究上の知見]
  CLI --> Core[実験管理と評価の共通処理]
  Core --> DB[(ローカルSQLite)]
  Core --> Jobs[Job Backend]
  Jobs --> Local[ローカル実行]
  Jobs -. 将来 .-> Cloud[Vertex AI / Cloud Run Jobs]
  Local --> Artifacts[Artifact Storage]
  Cloud --> Artifacts
  Core --> Eval[固定Evaluator]
  CLI --> AEAdapter[AlphaEvolve Adapter]
  AEAdapter <--> AE[Google Cloud AlphaEvolve]
  AEAdapter --> Jobs
~~~

この図は処理の呼び出し関係を示し、Pythonのimport方向を表すものではない。

### 4.1 コンポーネントと責務

| コンポーネント | 担当する処理 | 担当しない処理 |
| --- | --- | --- |
| ML Research Skill | 研究計画、EDA、仮説、次の実験、レビュー、レポート | メトリクスの確定、DBの整合性 |
| コーディングエージェント | SQL・コードの実装、実験の呼び出し、結果解釈 | 固定Evaluatorの改変、認証境界の変更 |
| ML Project | データ加工、学習、推論、タスクに固有のArtifact生成 | 実験ID・採用状態・評価履歴の管理 |
| mlh Core | 実験の定義、状態、比較可否、採用判断の記録 | モデル方式の選択、Google Cloud固有API |
| Job Backend | ジョブの投入、照会、中止、結果回収 | モデルの精度や研究上の価値の判断 |
| Evaluator | 評価対象・正解・指標に基づく検証済みスコアの算出 | 学習・特徴量生成 |
| Registry | 実験条件・状態・結果・系譜の永続保存 | 研究上の結論の長期記憶 |
| tk | 研究タスク、意思決定、発見、未解決問題 | Runごとの数値・学習ログ |
| AlphaEvolve Adapter | サービス固有のProgram・評価結果とmlhの相互変換 | 進化的探索の実装 |

CLIからCoreへ処理を依頼し、Coreは必要なJob BackendやEvaluatorを利用する。CoreのデータモデルにBigQuery、GitHub、AlphaEvolve等のSDK固有型を持ち込まない。実装の組み立ては実行入口の近くで行う。

### 4.2 SkillとPythonを分ける理由

Skillだけであれば、エージェントは研究手順を柔軟に変更できる。しかし、同一実験の二重起動や評価条件の書き換えを、自然言語の指示だけで確実に防げない。

反対にPythonが研究フェーズ全体を制御すると、データの異常を発見して問題設定を見直す場合にも、制御フローの追加実装が必要になる。

そこで研究上の判断はエージェントに任せ、実験状態・評価・保存・障害復旧をPython側へ分ける。Skillの数は最初は1つにし、研究・実験・レビュー・統合の手順を必要に応じて参照する。

## 5. 一連の処理例

前述の需要予測モデルを作る場合、次のように動作する。

~~~mermaid
sequenceDiagram
  participant A as Agent + Skill
  participant D as BigQuery / Data
  participant H as mlh
  participant J as Job Backend
  participant E as Evaluator
  participant T as tk

  A->>D: 許可範囲を調査、SQLを作成
  D-->>A: 学習データ・データ品質
  A->>H: 評価ProtocolとSnapshotを登録
  A->>A: モデルを実装、Candidateを保存
  A->>H: CandidateとRun設定を登録
  H->>J: ジョブ投入
  J-->>H: Artifactと実行結果
  H->>E: 固定した評価条件で採点
  E-->>H: 検証結果
  H-->>A: Runの指標・比較
  A->>T: 必要な知見・判断だけ記録
  A->>A: 次の実験または統合を選択
~~~

研究途中で欠品に由来するデータの偏りが見つかれば、エージェントは学習データの生成方法や評価設計を再検討してよい。評価設計を変更した場合は新しいEvaluation Protocolを登録し、変更前の数値との直接比較を行わない。

## 6. データ取得と評価方法

### 6.1 データ取得の範囲

データの利用範囲はML Projectごとに定義する。例えばBigQueryのsales、inventory、productsという3つのデータセットを参照可能とし、SQLの内容はエージェントが決める。

~~~yaml
data_access:
  provider: bigquery
  billing_project: example-ml-research
  location: asia-northeast1
  allowed_datasets:
    - example-retail.sales
    - example-retail.inventory
    - example-retail.products
  max_bytes_billed: 10000000000
~~~

上記は架空の設定例である。許可範囲の宣言だけではアクセス制御にならない。実際には読み取り専用のIAM権限、実行主体の認証、クエリ費用上限を設ける。

学習データの抽出期間、JOIN、集計粒度、欠品補正、サンプリングは変更してよい。実行SQLとパラメータ、参照テーブル、スキーマ、取得時点、保存したデータの識別情報を記録する。同じSQLでも元テーブルが更新されれば結果が変わるため、SQL文字列だけをデータの識別子にはしない。

### 6.2 Evaluation Protocol

Evaluation Protocolは、モデル同士を比較する際の共通条件をまとめた定義である。

固定する項目は評価対象の母集団・予測キー、正解ラベル、評価用SQLとSnapshot、学習・検証の分割規則、利用可能な特徴量の時点、評価指標と集計方法、Evaluatorのバージョンとする。

学習データはCandidateやRunごとに変更してよい。ただし、評価データの正解ラベルを学習側へ渡してはならない。特に最終Holdoutは、反復的なモデル選択に利用するValidationと分ける。

評価Protocolの内容が変わった場合は新IDを発行する。異なるProtocolで得たスコアを同列に比較しない。必要なら、既存のベースラインを新しいProtocolで再評価する。

### 6.3 共通の実行・出力形式

Harnessはモデル固有のprepare、train、predictというメソッドの実装を要求しない。Run Specが実行するコマンド・入力・設定・出力先を指定し、ML Projectが評価に必要なArtifactを生成する。

評価に必要なArtifactの種類はProtocol側が指定する。例えば需要予測では予測キーと予測数量の表、推薦では対象ユーザーと候補商品のスコア、最適化では目的値と制約充足の結果などを想定する。

候補モデルのコードから出力したスコアは参考値として保存できるが、採用判断に使う検証済みスコアは独立したEvaluatorが計算する。

~~~yaml
run:
  candidate_id: candidate-lgbm-02
  train_dataset_id: dataset-train-03
  evaluation_protocol_id: forecast-cv-01
  seed: 42
  parameters:
    learning_rate: 0.05
    num_leaves: 63
    objective: regression_l1
  execution:
    backend: local
    command: ["uv", "run", "python", "-m", "forecast.train"]
    timeout_seconds: 3600
~~~

このRun Specは形式の例であり、正式なJSON Schemaは実装時に確定する。デフォルト値を含めて全設定を実行前に確定し、保存する。

## 7. 実験を管理する概念

実験をコードの変更と実行条件の変更に分けて追跡する。

| 単位 | 意味 | 例 |
| --- | --- | --- |
| Approach | 独立して改善する解法の系列 | LightGBMの直接予測、Transformerの時系列予測 |
| Candidate | コードと設定項目が固定された実装 | 価格・在庫特徴量を追加したLightGBMのコード |
| Run | Candidateと具体的な設定・データ・評価条件を組み合わせた一つの評価 | learning_rate=0.05、seed=42、5-fold CV |
| Attempt | Runを実際に起動した1回分の処理 | 正常終了、タイムアウト、障害後の再試行 |
| SearchCampaign | ひとまとまりの探索活動 | OptunaのStudy、AlphaEvolveの探索、Codexの改善試行 |

Approach、Candidate、Run、Attemptは親子関係を持つ。SearchCampaignは複数CandidateとRunを横断してまとめるため、別の参照として扱う。

### 7.1 CandidateとRunの境界

コードを変更したときは新Candidate。例えば新しい特徴量生成処理やカスタムLossを実装すれば、実装が変わる。

一方、学習率、木の深さ、Lossの選択、特徴量の有効・無効、seedなどを既存の設定項目で切り替える場合は、同じCandidateの新Runとする。Lossの変更だけを理由にApproachは分けない。

学習データのSQLだけを変更して新しいSnapshotを生成した場合、モデルコードが同じなら新Runで比較できる。SQL生成処理をモデルのコードに組み込んでいて、その実装を変更した場合は新Candidateになる。

1つのRunは、指定した条件による完結した評価を表す。5-fold CVなら5回の学習をまとめて1 Runとし、foldごとの指標はその結果に含める。Optunaの各Trialは通常それぞれ新Runになる。

### 7.2 実験の記録と状態

RegistryはローカルSQLiteを初期実装に使用する。Approach、Candidate、Run、Attempt、SearchCampaign、Acceptance、Promotion、DatasetSnapshot、EvaluationProtocolを保存する。

Candidateは不変のコードSnapshotとして保存する。Git commitか、Content Digest付きのコードBundleで参照する。研究ブランチの現在のHEADを使って実行中のCandidateが変わることは許さない。

Runの確定パラメータ、データ、評価Protocolは実行後に変更しない。verifiedとなったスコアも上書きせず、再評価が必要なら新Runを作る。

Runの大まかな状態はcreated、running、verifying、verified、invalid、failedとする。実行先の状態を確認できない場合はunknownとして扱い、照合するまで成功・失敗を決めない。

AttemptにはジョブID、開始・終了時刻、実行環境、終了状態を残す。障害回復による再試行は同じRunの別Attemptとし、結果のばらつきを測る独立実験は新Runとする。

### 7.3 検証・採用・統合

研究上の意思決定を次の3段階に分ける。

| 段階 | 意味 |
| --- | --- |
| Verified | 固定したProtocolでRunを評価できた |
| Accepted | 有望なRunとして研究系列で採用した |
| Promoted | 採用元の内容を整理・再評価したコードがmainへ統合された |

Acceptの対象はCandidateだけでなくRunとする。採用したハイパーパラメータや学習データも特定するためである。複数ApproachのRunを同時にAcceptedにでき、未採用の研究を停止する必要もない。

Promote時にはmainから統合用ブランチを作り、必要な実装だけを持ち込む。設計Skillに従ってコードを整理し、統合後のコードを新Candidateとして再評価する。元のAccepted Run、統合後Run、PRとmerge commitを関連付ける。実験ブランチをそのままmainへmergeすることは標準手順にしない。

## 8. Git branchとworktreeの使い分け

worktreeは独立した作業ディレクトリであり、CandidateやRunそのものではない。1つの研究ブランチには複数のCandidateを保存できる。

新しいApproachとするのは、次のいずれかが変わる場合である。

- 学習アルゴリズムの体系が変わる。例：LightGBMからTransformerへ切り替える。
- 学習・推論の主要な段階が変わる。例：単一予測モデルから二段階予測へ変更する。
- 学習問題を構成する方式が本質的に変わる。例：売上回帰とは別に潜在需要を推定する段階を追加する。

探索するパラメータの数が増えても、それだけでは新Approachにしない。新特徴量や新Lossの実装は原則同じApproachの新Candidateとする。

worktreeを新設するのは、新Approachを始める場合、または同じApproach内で互いに競合するコード変更を並行して継続する場合とする。短い試行には一時的なworktreeを使ってもよい。

~~~text
main
├── approach/lgbm
│   ├── Candidate L1 → Run 1, Run 2
│   ├── Candidate L2 → Run 3
│   └── variant/lgbm-alt-features
│       └── Candidate L3 → Run 4
└── approach/transformer
    └── Candidate T1 → Run 5, Run 6
~~~

この図は研究系列の例であり、Gitの実際のディレクトリ構成ではない。worktreeの削除後も、採用に必要なcommitやコードBundleと実験Artifactは保持する。AlphaEvolveが作る大量のProgramには、それぞれ専用worktreeを作らない。

## 9. AlphaEvolveとの接続

AlphaEvolveはGoogle Cloudが提供する進化的なコード探索サービスであり、動作するSeed Programと評価関数を必要とする。一般的なコーディングエージェントの代わりに、自然言語だけからベースラインを作る用途には使用しない。

最初のバージョンからAlphaEvolve Adapterを実装対象とする。ただし、サービスとの接続で問題が判明した場合はAdapterの構造を変更する。

### 9.1 接続方法

Google公式のPythonクライアントが提供するControllerを利用し、候補Programの取得、評価、スコアの返却を行う。Harnessは次の変換を担当する。

| AlphaEvolve | Harness |
| --- | --- |
| Experiment | SearchCampaign |
| Program | Candidate（コードBundle参照） |
| Program Evaluation | Run |
| 評価ジョブの試行 | Attempt |
| Scores / Insights | 評価指標・診断情報 |

動作するベースラインからSeed Programを準備し、変更可能なコード範囲を指定する。生成されたProgramを不変のCandidateとして保存し、共通Evaluatorで評価する。小さいほどよいWMAPEなどの指標は、AlphaEvolveが使用する最大化方向へAdapter内で変換する。

AlphaEvolveがハイパーパラメータをコードに直接書き込んだ場合は新Candidateになる。外部設定だけを変えた場合は同じCandidateの別Runにできる。

公式サービスが持つProgram DBの内容を、Gitへ1件ずつ複製しない。有望候補を選んだときに、必要ならGit研究ブランチへ取り込む。

### 9.2 Controllerの運用

初期はローカルControllerを使用する。候補生成はGoogle Cloud側で行われるが、評価結果を返すControllerは起動している必要がある。ローカルPCが停止すると、反復的な評価が継続できない場合がある。

完全な無人探索が必要になったら、Controllerもリモート環境へ移す。これは個別の学習ジョブをリモートへ送る機能とは別の拡張である。

Google Cloudの利用権限がない環境では、Mockを使ったAdapterテストと実サービスとの接続確認を区別する。Mockの成功だけでlive接続済みとはしない。

## 10. ローカル実行とリモート実行

初期の操作とRegistryの管理はローカルで行う。重い学習・予測・評価はJob Backendを通して別環境へ送れるようにする。

~~~python
class JobBackend(Protocol):
    def submit(self, spec: JobSpec) -> JobHandle: ...
    def status(self, handle: JobHandle) -> JobStatus: ...
    def cancel(self, handle: JobHandle) -> None: ...
    def collect(self, handle: JobHandle) -> ArtifactManifest: ...
~~~

これは必要な操作を示す概念インターフェースで、実装時に戻り値やエラー処理を確定する。最初はLocal Backendを実装し、用途に応じてVertex AI Custom JobsやCloud Run Jobsを追加する。ローカルのコマンド実行にはsubprocessやInvoke等を選べるが、外部APIをそのライブラリに依存させない。

### 10.1 非同期ジョブの復旧

リモートへ投入するときは先にRun/AttemptをRegistryへ登録し、返された外部ジョブIDを保存する。同じIDをクラウドジョブのラベル等にも付け、ローカル側の保存が途中で失敗しても再発見できるようにする。

ジョブが完了したら、ローカル側が状態とArtifactを取得し、Evaluatorによる検証を終えてからRunをverifiedにする。PC停止中にリモート学習が完了した場合にも、再開後のsyncで追跡できるようにする。

同じ要求の再送でジョブが二重起動しないようにする。一方、同じ条件を使った独立の再現実験は別Runとして明示的に作成できるようにする。

初期のSQLiteは単一主要writerで利用する。複数の独立したエージェントが別マシンから同時に書き込む必要が出たら、クラウドRegistryを検討する。

## 11. tkとエージェントへの情報の引き継ぎ

[tk / tasukura](https://github.com/mixidota2/tasukura) は、コーディングエージェント向けのローカルタスク管理CLIである。タスクの進捗をprogress_logsに保存し、後から参照すべき判断や発見をtyped recordsとして管理できる。

ML Research Harnessでは、研究の進捗・発見・未解決仮説をtkへ記録する。個々の学習実行やスコアはRegistryで扱うため、全Runをtkのタスクにする必要はない。

新しいエージェントセッションでは、研究目的、現在の評価Protocol、有効なtkの知見、最良のVerified/Accepted Run、関連する失敗、次の実験、残り予算をまとめて渡す。学習ログ全文を毎回コンテキストに含めず、必要になったときにRun IDから取得する。

例えば「価格ラグを加えても改善しなかった」という結果はRunに残す。「欠品日に販売数量が需要を過小評価する可能性がある」という研究上の仮説は、検証状況を添えてtkに残す。仮説が否定された場合はtkの既存の履歴管理機能を使って更新する。

## 12. 配布方法とPythonコードの構成

SkillとPython CLIは同じ実装リポジトリで管理する。ただしPythonツールはSkillがなくてもテスト・実行できる構成とする。エージェント固有のプラグイン設定は配布先に応じて用意する。

~~~text
ml-research/
├── pyproject.toml
├── AGENTS.md
├── skills/
│   └── ml-research/
│       ├── SKILL.md
│       └── references/
│           ├── research.md
│           ├── experimentation.md
│           ├── branching.md
│           ├── promotion.md
│           └── reporting.md
├── src/
│   └── mlh/
│       ├── cli.py
│       ├── experiments/   # 候補・Run・採用状態
│       ├── evaluation/    # Protocolと評価検証
│       ├── execution/     # ジョブ投入・結果回収
│       ├── data/          # データSnapshotの管理
│       ├── git/           # branch/worktreeの操作
│       ├── alphaevolve/   # Google Cloud専用Adapter
│       └── context.py
├── tests/
├── examples/
│   └── toy_forecasting/
└── docs/
    └── design.md
~~~

上記は責務の配置例である。実装が小さいうちはファイルを平坦に置き、独立した変更理由が生じたときにサブパッケージへ分けてよい。

個々のML Projectは既存のディレクトリ構成を維持する。モデル固有の学習・推論・前処理は、実際の変更単位に沿って同じ場所へ置く。例えばLightGBM固有の特徴量生成と学習処理は同じApproach内にまとめ、共通化する根拠が確認できたコードだけを外へ移す。

この配置方針は [design-python-code](../../skills-local/.codex/skills/design-python-code/SKILL.md) に従う。

### 12.1 CLIの候補

| 操作 | 役割 |
| --- | --- |
| mlh init | 既存ML Projectへ設定を追加する |
| mlh candidate capture | 実装を不変Candidateとして保存する |
| mlh run submit / status / sync / collect | Runの実行、状態照会、結果回収 |
| mlh compare | 比較可能なRunの評価結果を集計する |
| mlh approach create / branch create | 新しい研究系列やworktreeを作る |
| mlh accept | Verified Runを理由付きで採用する |
| mlh promote prepare | mainへの統合作業を準備する |
| mlh context | 次のエージェントセッションへ渡す情報を作る |
| mlh alphaevolve start / status / collect | AlphaEvolveの探索と結果の取り込み |

コマンド名、引数、JSON Schema、例外形式は実装時に確定する。CLIから同じPythonの処理を呼ぶ形にし、将来NotebookやAPIが必要になってもMLロジックを複製しない。

## 13. セキュリティ、費用、障害への対処

### 13.1 データと権限

データアクセスは許可されたデータセットに限定する。学習コードを実行する認証情報から、保護された評価用の正解ラベルへアクセスできないようにする。資格情報をGit、実験ログ、Artifactに保存しない。SQLを自由に変更できることを、アクセス権限まで自由であることと混同しない。

### 13.2 計算費用

実験数、時間、CPU/GPU、BigQuery処理量、AlphaEvolveのAPI利用量に予算上限を設定する。外部サービスの費用が取得できない場合は推定と実測を区別する。予算超過時に新しいジョブを投入しない。

### 13.3 障害・不整合

| 事象 | 必要な動作 |
| --- | --- |
| 同一Runの投入要求が重複 | 冪等キーで確認し、意図しない二重起動を防ぐ |
| リモート投入後にローカルが停止 | 外部ジョブIDやラベルから照合し、状態を復旧する |
| 学習が完了しても予測Artifactが欠けている | verifiedにせず、失敗の理由を残す |
| 評価Protocolが変わった | 旧スコアとの比較を拒否する |
| コードやデータの参照先が失われた | 再現可能と表示せず、保存・保持方式を修正する |
| AlphaEvolveのAPIや権限が使えない | Adapterを原因付きで失敗扱いし、通常の研究機能は利用可能にする |

## 14. 検討した代替案

| 代替案 | 今回採用しなかった理由 | 再検討する条件 |
| --- | --- | --- |
| Skillだけで実験を管理する | 評価の固定や二重実行防止を自然言語の指示だけでは保証できない | 使い捨ての小規模PoC |
| Python製Supervisorが研究の順序を制御する | 既存のコーディングエージェントの計画・実装機能と重複する | 無人でセッションを跨ぐ研究継続が必要になったとき |
| train/predictの固定関数API | 二段階・再帰・アンサンブルなど異なる構成を試しにくい | 対象が特定のMLパイプラインへ絞られたとき |
| 1実験につき1worktree | パラメータ実験まで作業ディレクトリが増える | 実験ごとの完全隔離が必要な短期用途 |
| 実験コードをそのままmainへ統合する | 暫定処理や使わない分岐が残りやすい | 単純な既存コード修正 |
| 初期からクラウドRegistryを使う | 単一開発者のローカル運用では認証・運用が増える | 複数writerや共同研究が必要になったとき |
| テンプレートリポジトリを主な配布手段にする | 既存ML ProjectへHarnessの更新を反映しにくい | 新規Projectの共通構造が安定したとき |

## 15. 実装計画と受け入れ条件

最初から全機能を実装しない。以下の順序で、動作確認できた機能を増やす。

### Phase 0：実行・評価の最小実装

toy forecastingを使い、同じCandidateから異なるハイパーパラメータの2 Runを実行する。コード・設定・データ・評価Protocolを保存し、固定Evaluatorで比較できるところまで作る。

この段階の完了条件は、同じ条件での再評価、評価条件の違いの検出、失敗したAttemptの記録が確認できること。

### Phase 1：研究ワークフローと本流への統合

ML Research Skill、tk連携、データアクセス設定、Approachとworktreeの分岐、Accept、Promote、研究レポートを実装する。

同じApproach内の特徴量変更ではCandidateが増え、ハイパーパラメータ変更ではRunだけが増えることを確認する。複数Approachの研究を続けながら、一方の成果だけをmainへ統合する。

### Phase 2：AlphaEvolveとの統合

最小SeedとEvaluatorで、Program取得、Candidateとしての保存、Runの評価、scores/insights返却、選抜候補のGitへの取り込みを確認する。サービス利用権限がない環境ではMockの検証結果と実サービスの未検証状態を分ける。

### Phase 3：リモート学習の実装

用途に合うJob Backendを一つ追加する。リモートの投入・終了・回収、PC停止後の状態照合、重複起動の防止、Artifact欠落の扱いを確認する。

### 15.1 横断的な完了条件

完成したシステムは、次の状態を再現できなければならない。

- 異なるアルゴリズムのApproachを並行して育てられる。
- 一つのCandidateに複数のRunを登録し、ハイパーパラメータを完全に復元できる。
- 同じ評価条件で得た数値だけを比較できる。
- Verified Runを複数Acceptedにでき、Promote後のコードを再評価できる。
- 一時的な実験ブランチを消しても、必要な結果と採用したコードを再現できる。
- 失敗した実験や途中で方針を変更した理由が最終レポートに残る。
- AlphaEvolveに接続しなくても、通常のCodexを使う研究機能が動作する。

## 16. 未決事項と実装時の確認

基本設計を作り直す必要はないが、次の仕様はまだ確定していない。

| 未決事項 | 確認方法・決める時期 |
| --- | --- |
| Artifact Manifestの正式Schema | Phase 0で需要予測を使い、将来のランキング・最適化への拡張も確認 |
| Evaluation Protocolの凍結と最終Holdoutの権限 | Phase 0〜1で認証・データ境界を実証 |
| DatasetSnapshotの物理形式・保存期間 | データ規模とGCS費用に応じて選択 |
| Job Backendの冪等性と状態復旧 | Phase 3の障害注入テストで決定 |
| AlphaEvolveの現行SDK・契約・API | Phase 2で実サービスに接続して検証 |
| CodexとClaude Codeの配布互換性 | プラグイン導入テストで確認 |
| 多数のCandidate Bundleの削除規則 | 保存費用と採用履歴の保持条件から決定 |
| Pythonの最終ディレクトリ分割 | 実装後に変更理由・依存関係を調査して確定 |

現状は実装前の設計レビューであり、コードのimport関係、API互換性、実行コスト、実データの取扱いは未検証である。未検証を動作確認済みと記述しない。

## 17. 仕事用の開発環境への引き継ぎ

この設計書は、会話履歴を参照せずに実装を開始できることを目的としている。

実装者は最初にPhase 0のtoy forecastingを作る。CandidateとRunの区別、Protocolでの採点、Artifact保存、失敗の記録を動作確認してから、Git分岐・Skill・AlphaEvolveを追加する。

この文書が置かれているdotfilesリポジトリは設計書の保管先であり、ML Research Harnessの実装先ではない。実装コードは別リポジトリで管理する。Pythonコードの構成は [design-python-code](../../skills-local/.codex/skills/design-python-code/SKILL.md)、新しいSkillの検証は [create-validated-skill](../../skills-local/.codex/skills/create-validated-skill/SKILL.md) に従う。

## 参考資料

- Google Cloud： [AlphaEvolve概要](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/overview)、[使用方法](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/use-alphaevolve)
- [Google Cloud公式AlphaEvolveクライアント](https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud)
- [Git worktreeの公式ドキュメント](https://git-scm.com/docs/git-worktree)
- [CSIRO Kaggleのエージェント活用事例](https://github.com/Chihiro-n/csiro-biomass-agentic-solution)
- [AIDE](https://github.com/wecoai/aideml)、[MLEvolve](https://github.com/InternScience/MLEvolve)
- [tk / tasukura](https://github.com/mixidota2/tasukura)
