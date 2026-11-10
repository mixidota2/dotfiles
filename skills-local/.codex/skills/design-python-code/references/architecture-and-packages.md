# Architectureとpackage境界

## Packageを作る条件

次の複数が成立するとき、独立packageを候補にする。

- 独立して説明できるcapabilityまたはsubdomainがある。
- 固有の状態、不変条件、用語、公開操作を持つ。
- 他領域と異なる変更理由・release cadence・外部依存を持つ。
- 内部実装を隠す価値があり、consumerとの契約を定義できる。
- 削除または置換時の影響範囲を閉じ込められる。

単にfileが多い、技術種別が同じ、担当者が違う、将来増えそうという理由だけでは分けない。

## 境界の調査

treeだけで判断せず、次を確認する。

- executable entrypoint、CLI command、web handler、job定義
- import graphと循環import
- DI、factory、bootstrap、framework container
- database table/model、cache、file、eventのowner
- public import、`__all__`、documented API
- testが示す契約とfixtureの共有範囲
- 設定、Secret、environment variableの注入方法

## 標準5層アーキテクチャと依存方向

複数の専門機能や外部I/Oを持つPythonアプリケーション・パッケージは、トップレベルの粒度を揃えた次の5層構造を基準にする。

```text
src/<package_name>/
├── models/               # 1. 共有データモデル（複数モジュールが共通で使うデータの形のみ・処理ロジックなし）
├── <domain_modules>/     # 2. 専門モジュール群（各領域の計算・検証・変換のみ。他の専門モジュールやDBを知らない）
├── infra/                # 3. インフラ層（DB・ファイルI/O・外部API・LLM SDKとの入出力のみ）
├── use_cases/            # 4. ユースケース層（専門モジュールとinfraを順番に呼ぶ手続き的ワークフロー）
└── api/ , cli.py         # 5. 入口（HTTPルーター・CLI・AIツール定義。入出力変換とDIのみを行いuse_casesを呼ぶ）
```

### 1. データモデルの配置基準（共有モデルの集約＋内部型の内包）

- **共有データモデルは`models/`に集約する**：複数の専門モジュール・`use_cases/`・`infra/`をまたいで受け渡す中核のデータ構造（ドメインエンティティ、値オブジェクト、共有契約）は、各パッケージの奥に`model.py`等で散らさず、トップレベルの`models/`に概念（名詞）ごとのファイルでまとめる。
- **モジュール固有の内部型は各モジュール内に閉じる**：単一の専門モジュール内部でしか使わない設定・中間データ型（ソルバー内部構造やアルゴリズム固有の設定など）や、HTTP境界専用のRequest/Responseスキーマは`models/`に混ぜず、そのモジュール内に置く。

### 2. 専門モジュール同士の独立と階層粒度の統一

- 各専門モジュールは`models/`の型を受け取り、自身の専門領域の計算・検証・変換を行って返す。
- **専門モジュール同士は原則として互いにimportしない**。ある業務モジュールの中に他モジュールの呼び出しやDB保存まで詰め込んでGodパッケージ化させない。
- トップレベルに並べるパッケージは同じ粒度（独立した専門領域）に揃える。1つのモジュールからしか使われない下位部品はトップレベルに並列配置せず、そのパッケージ内部に収める。
- アプリ本体の責務ではない一時的な比較・検証処理は`scripts/`に切り出す。

### 3. ユースケース層（`use_cases/`）によるオーケストレーション

- 「保存済みデータを取得し → 専門モジュールAで計算し → 専門モジュールBで検証・更新し → `infra/`で永続化・ログ記録する」といった**複数モジュールをまたぐ手続き的ワークフローは、専門モジュールの中にも`api/`の中にも書かず、`use_cases/`に置く**。
- Web API（`api/`）、AIエージェントのツール、CLI（`cli.py`）が同じ業務操作を実行するときは、同じ`use_cases/`を呼び出す。これにより、`api/`が多数の業務モジュールに直接依存する密結合と、入口ごとの手続き重複を防ぐ。

## 変更理由をvariantへ閉じ込める

`models/`、`features/`、`services/`、`repositories/`、`configs/`のようなtechnical layerだけで分けると、一つのalgorithmやproviderの変更が各directoryへ散ることがある。変更シナリオでこの波及を確認した場合は、次の軸を候補にする。

```text
capability → processing stage / use case → algorithm・provider等のvariant
```

例えば推薦の候補生成でiALS、ItemKNN、Two Towerを交換するなら、algorithm固有の設定、学習、推論、artifact変換を同じsliceへ置く。

```text
recommendation/
├── public.py
└── candidate_generation/
    ├── use_cases/
    │   ├── train.py
    │   └── generate.py
    └── algorithms/
        ├── ials/
        │   ├── config.py
        │   ├── trainer.py
        │   ├── predictor.py
        │   └── adapter.py
        ├── item_knn/
        └── two_tower/
```

- 既存iALSの計算方法を変える場合は、原則として`ials/`とそのtestへ変更を局所化する。
- 新しいalgorithmを追加する場合は、新しいsliceに加えてcomposition rootまたは一つのresolverだけを変更候補にする。OCPを「既存fileを一切変更しない」とは解釈しない。
- algorithm間で本当に同じ意味と契約を持つ前処理だけを共有する。実装都合の重複排除でalgorithm固有のfeature処理を共通moduleへ引き上げない。
- variantが少なく選択も単純なら`match`や明示的factoryで十分である。callerへ分岐が散る、追加頻度が高い、外部plugin登録が必要と確認できた場合だけRegistryを導入する。

この形を固定templateにはしない。algorithm変更が複数の処理段階を必ず一体で変えるならalgorithmを上位境界にし、処理段階が独立して変わるなら上記の順序を保つ。実際のco-change、公開契約、state/artifact ownerで順序を決める。

## Use caseを複数の実行形態から共有する

CLI、Notebook、HTTP、batch、workflow orchestrator等が同じ目的を実行する場合、各entrypointに業務処理やML処理を複製しない。frameworkを知らないuse caseを先に定義し、entrypointはinput変換と呼び出しに留める。

```text
CLI ─────────┐
Notebook ────┼──> use case ──> domain / algorithm contract
Pipeline step┘            └──> port
                                  ↑
                         infrastructure adapter
```

- use case: algorithm、保存、評価等を利用者目的の単位で組み合わせる。
- infrastructure: cloud SDK、MLflow、database、object storage等との変換を持つ。
- pipeline: use caseの順序、依存関係、resource指定、retry等の実行制御だけを持つ。
- experiment: 本番と同じuse caseを別の設定・dataset参照で呼ぶ。production logicをcopyしない。
- composition root: 実行環境に応じた具体algorithm・adapter・設定を組み立てる。

ローカル実行可能なapplication coreを先に作り、KFP、Vertex AI、Airflow等のorchestrator固有型をuse caseやalgorithmへ流入させない。ただし、既存projectに安定した実行方式と境界がある場合は、その方式を再利用する。

## 依存方向

- consumerから公開契約へ依存させる。
- domain decisionをframework、ORM、HTTP、vendor SDKへ依存させない。
- adapterはconsumerが必要とする契約を実装する。
- composition rootだけが契約と具体実装の両方を知り、組み立てる。
- 循環importを遅延importや`TYPE_CHECKING`だけで隠さず、責務または契約のownerを見直す。

依存矢印を示すときは、import、call、data flowのどれかを明記する。

## 提案成果物

最低限、次を示す。

```text
Proposed tree
- package/module: 責務 / 非責務 / 所有状態 / 公開操作

Dependency direction
- caller -> public contract
- adapter -> contract
- bootstrap -> contract + adapter

Change scenarios
- scenario: 変更箇所 / 波及してはいけない箇所

Trade-off
- 得るもの / 失うもの / 再検討条件
```

大規模移行では、最終treeだけでなく中間状態、互換性、切替順を示す。一括移行を既定にしない。
