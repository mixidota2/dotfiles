---
name: consulting-slide
description: スライド設計規約 slide-rules.md を核に、経営会議・提案・分析報告品質のスライドを作るスキル。工程ごとに必要な規約だけを scripts/rules.py で引き、65型のHTMLパーツ集から new_deck.py で組み（既定はカインズ緑＋ゴシック体、--skin warm/cool・--font serif で切替可）、check_all.py の機械チェック FAIL 0 と PDF 画像目視・独立レビューで仕上げる。PPTX を明示されたときだけ html_to_pptx.py で変換し、既存 PPTX への差し込み時はマスター上に直接組む。「スライドを作って」「提案デッキにして」「分析結果を報告資料にまとめて」「この pptx にページを足して」などの依頼で使う。
---

# コンサル型スライド作成スキル

主軸は規約の正典 [references/slide-rules.md](references/slide-rules.md)。ただし最初に全文を読み込まない。**工程ごとに `scripts/rules.py --phase <工程>` で、その工程の要点（数千字。出力は切らずに全部読む）を読み、詳しい根拠が要る項だけ `--sec` で引く。** 部品は規約を満たすことを確かめた20種類（`new_deck.py --list`）から選び、検査で出た指摘は、指摘に書かれた節だけを引いて直す。

成果物は HTML（16:9・1 section = 1スライド）と、Chrome で印刷した PDF（`snapshot.py`）。

## パスと実行環境

この `SKILL.md` が置かれているディレクトリを `<SKILL_DIR>` とする。どの作業ディレクトリにいてもスクリプトは `<SKILL_DIR>/scripts/...` で呼び出し、生成物（`*.html`・`*.pdf`・`*.pptx`・`sources.md`・`qa.md`）はすべて**現在の作業フォルダ（依頼者の指定先）**に出力する（`<SKILL_DIR>` の中には書き込まない）。

- **標準ライブラリ・ローカル Chrome のみで動くスクリプト**（追加 npm パッケージ不要）:
  - `python3 "<SKILL_DIR>/scripts/rules.py"`
  - `python3 "<SKILL_DIR>/scripts/new_deck.py"`
  - `python3 "<SKILL_DIR>/scripts/check_all.py"`（内部で `check_deck.py` と `node check_layout.mjs` を実行）
- **Python 外部パッケージを使うスクリプト**（PEP 723 メタデータ内蔵のため `uv run` でそのまま動く）:
  - PDF 化＋目視用シート画像生成: `uv run "<SKILL_DIR>/scripts/snapshot.py" mydeck.html`
  - HTML → 編集可能 PPTX 変換: `uv run "<SKILL_DIR>/scripts/html_to_pptx.py" mydeck.html`
  - PPTX の規約検査・レイアウト検査・書式計測: `uv run "<SKILL_DIR>/scripts/check_deck.py" mydeck.pptx`、`uv run "<SKILL_DIR>/scripts/check_deck_layout.py" mydeck.pptx`、`uv run "<SKILL_DIR>/scripts/measure_deck.py" house.pptx`

## 配色と書体の選択

`new_deck.py` の既定は **カインズ緑＋ゴシック体**（`--skin cainz --font sans`）になっている。資料の相手・用途に応じて `--skin` と `--font` で切り替えられる。

| オプション | 選択肢 | 使いどころ |
| --- | --- | --- |
| `--skin` | `cainz`（既定） / `warm` / `cool` | `cainz`＝カインズ規定の緑（`#006438` / `#005032` / `#EAEFEC`）。`warm`＝生成りの地・濃茶の暖色系。`cool`＝白地・ネイビー系 |
| `--font` | `sans`（既定） / `serif` | `sans`＝見出し・本文ともゴシック体。`serif`＝見出し明朝体・本文ゴシック体 |

意味を持つ色（悪化・✕の赤など）はスキンにかかわらず変えない。2系列の区別はメインカラー×グレーの2色に抑え、製品UIのスクリーンショットは無加工で載せる。制作ツールの名称はどのページにも表示しない（表示されていると `check_deck.py` が FAIL にする）。

## 読むもの・使うもの

| 工程 | 読む・使う | 読まない |
| --- | --- | --- |
| 1. 定義 | `rules.py --phase plan`（§0・§2・§3・デッキの構造） | — |
| 2. 調べる | BigQuery・社内一次資料・公式統計などの一次情報。確かめた数字は `sources.md` に控える | 検索結果の抜粋だけで数字を拾わない |
| 3. ストーリーライン | 1 の内容と `new_deck.py --list`（部品の番号・型名・使いどころ） | パーツ集の原本・型カタログ全文 |
| 4. 骨組み | `new_deck.py --parts ...`。部品の作りを確かめたいときだけ `new_deck.py --show b09` | パーツ集の原本、`scripts/` の実装コード |
| 5. 中身を書く | `rules.py --phase build`、ページの形に応じて `--phase chart` / `table` / `process` | — |
| 6. 文章を仕上げる | `rules.py --phase text`、[references/ai-smell-lexicon.md](references/ai-smell-lexicon.md) | — |
| 7. 検査と直し | `check_all.py`（check_deck・check_layout・出典の照合）、指摘に出た節だけ `rules.py --sec 4.12 5.26` | 規約の全文 |
| 8. 最後の確認 | `rules.py --phase check`、`uv run snapshot.py` で作る PDF と全ページの画像 | — |
| 9. フレッシュアイ・レビュー | [references/content-review-prompt.md](references/content-review-prompt.md) | — |

- PPTX にするときは `rules.py --phase pptx`、登壇用のピッチは `--phase pitch` を工程1で足す。
- 社外提出前に顧客名・案件コードなどの残りを検査するときは `check_deck.py mydeck.html --forbid <禁止語リスト.txt>` を使う。
- `sources.md` には、調べた数字に加えて、試算・計画値も「前提と算式」と一緒に書く（例: 「3億円 計画値: 基盤1.2億＋人件費…」）。デッキの数字が控えに無いと `check_all.py` が WARN を出す。

## 手順

1. **定義する**: `python3 "<SKILL_DIR>/scripts/rules.py" --phase plan` を読み、目的・成果物の定義・スコープ IN/OUT を3〜5行で決める。依頼の背景（誰に・どの会議で・何を判断してもらうか）が分からなければ先に確認する。確認できないときは前提を置き、報告に明記する。
2. **調べる（必須）**: デッキに載せる数字・事例・固有名詞は、作る前に一次情報（社内実データ・議事録・公式発表・原典統計）で確かめる。確かめたものを、デッキと同じフォルダの `sources.md` に1行ずつ控える（数字・何の数字か・出典名・URLまたはテーブル名・時点）。見つからない数字は作らず、デッキには「要確認」と書く。
3. **ストーリーラインを書く（中身 → 形 → 部品の順）**: まず調べた材料から、1枚1行の主張（タイトル）の列を書く。部品の一覧はまだ見ない。次に、各行の形を材料の関係（比較・推移・内訳・因果・位置づけ・手順）から決める（§5.12）。最後に、決めた形に合う部品を `python3 "<SKILL_DIR>/scripts/new_deck.py" --list` から選び、合う部品が無ければ `--list --all`（詳細は [references/archetype-catalog.md](references/archetype-catalog.md)）を見るか規約の範囲で自由に組む。
   - 社名と数字を消しても別の組織の資料として通る列なら、型どおりになっている。材料に固有の発見（現場の事情・意外な数字・具体事例）を主役にしたページを立てるか、中身の薄いページを隣と統合する。
   - タイトルは数でなく中身で書く（「3本柱で進める」ではなく柱の名前を書く）。
   - 推移・構成比・分布・相関は必ず図にする。材料が目安いっぱい（表なら4行以上、グラフなら1本＋読み取り2〜3点、箇条なら主文＋詳細で6行以上）に届かない行は、隣の行とまとめる。枚数を稼ぐためにページを薄く割らない。
4. **骨組みを作る**: `python3 "<SKILL_DIR>/scripts/new_deck.py" --parts b01,m01,b09:axb,m05,b10 --title "資料名" -o mydeck.html`（別の配色・書体が指定されたときは `--skin warm|cool`・`--font serif` を添える）。CSS の結合・見出しの様式・ページ番号はスクリプトが揃える。
5. **中身をまとめて書く**: `rules.py --phase build` と `--phase text`、使う形に応じて `--phase chart`・`table`・`process` を読んでから、骨組みの全ページの伏せ字（`Text N`・`ラベル N`・`パーツNN｜…`）を実物に置き換えた完成版を、**1回のファイル書き込みで書く**。
   - 数字は `sources.md` に控えたものだけを使う。**試算は暗算せず `python3 -c` で計算し、式・入力・結果を `sources.md` に貼る。** 読み手は `sources.md` を見ないため、試算・計画値の前提と算式はそのページの注か付録にも書く。
   - 右上のラベルは、そのページの話題を日本語で書く。
   - 日本語は `explain-before-shorten` の原則に従い、まず口頭で伝わる説明に組み立ててから意味と条件を保って短く整える。「使う人」「ユーザー」のような書き手視点の相対呼称は使わず、具体的な役割名（店舗担当者、売場構成企画部の担当者、事業部、決裁者など）で主語・対象・述語を一意に言い切る。
   - 初稿の FAIL を防ぐ3原則: ①1つの箇条・セルに1文（§7.24）、②タイトルは数でなく中身（§4.103）、③中身の少ないページは文字を一段大きくするか前後と統合する（版面の40%超が空くと FAIL。§5.13）。
6. **文章を仕上げる**: `--phase text` と [references/ai-smell-lexicon.md](references/ai-smell-lexicon.md) の基準でタイトルを上から通し読みし、文章を直す。直しは必要なページだけ編集する。
7. **検査をまとめて回し、まとめて直す**: `python3 "<SKILL_DIR>/scripts/check_all.py" mydeck.html --sources sources.md` を回す。出た FAIL・WARN は、メッセージにある節を `rules.py --sec` で引いてから、ページごとにまとめて直す。
   - **指摘を消すために中身（数字・事例・行）を足さない。** 直しは形（大きさ・配置・分け方・統合）で行い、材料を足すなら手順2に戻って一次情報から調べたものだけにする。
   - **空きを消すために、部品の CSS を上書きして高さや間隔を伸ばさない**（`height:100%`・`flex:1`・`justify-content:space-around`・`min-height` などは間延びとして `check_layout` が FAIL を出す）。タイトルの大きさ（22pt）も書き換えない。空きの指摘に付く `zoom` の試算値（1.15倍まで）を使うか、「1.15倍でも収まらない」と出たページは前後と統合するか形を変える。
   - 直したらもう一度 `check_all.py` を回し、FAIL 0 で次へ進む。**WARN も直すのが既定。** 規約の例外条件に当たって残すときだけ、その条件（§番号）を `qa.md` に書く。
8. **PDF と画像で全ページを見る**: `rules.py --phase check` を読み、`uv run "<SKILL_DIR>/scripts/snapshot.py" mydeck.html` で PDF と4ページずつまとめた画像を作って全画像を目視する。§8 の「目視で確かめる基準」を1つずつ当て、怪しいページは `--page N` で大きく出して確かめる。崩れを見つけたら直して `check_all.py` を再実行する。**見た結果は `qa.md` にページごとに書く**（各ページで当てた基準・見つけた崩れ・直したか。見つからなかったページは「該当なし」）。
9. **フレッシュアイ・レビュー**（速度優先と指定されたとき以外）: 下の作法に従って実施する。

## 作業の作法（確認・レビュー・直し方・渡し方）

### 着手・合意の取り方

- **文言を先にテキストで合意してから作図する。** ストーリーライン（タイトル列）と各ページの「タイトル／結論／載せる内容／見せ方」の4項目を先に出して合意すると手戻りが少ない。依頼者がページ構成やタイトルを指定済みのときは組み替えずに使う。
- **背景が分からない資料はデッキにしない。** 誰に・どの会議で・何を判断してもらう資料かが未確定のうちは、まず1枚の論点整理かテキストで確認する。
- **量産の前に1枚だけ見せる。** 同じ型を5枚以上に展開するデッキは、代表の1枚を先に仕上げて合意してから横に広げる。
- **シリーズ物（定例・月次報告・続きの資料）は、相手に出した前回の最終版を土台にし、変わった箇所だけ直す。** 下書き版は土台にしない。体裁（型・配色・並び）は引き継ぎ、数字と進捗はその回の一次情報から取り直す。前の版で依頼者が消したページ・節・言い回しは作り直さない。

### フレッシュアイ・レビューとループ管理

1. 機械チェック FAIL 0・目視 QA のあと、[references/content-review-prompt.md](references/content-review-prompt.md) の指示文を、**作り方・使ったスキル・こだわった点を一切伏せて**文脈を共有しない別エージェントに渡し、デッキのファイル（HTML／PDF／PPTX）をそのまま読ませる。
   - 可能なら**複数モデルで並行に独立レビューさせる**（例: サブエージェントと `pi -p --no-session --model google-vertex/gemini-4-argon -t read,bash,grep,find,ls "..." < /dev/null` や `codex exec`）。両方が挙げた共通の指摘を優先して直し、片方だけの指摘は個別に採否を判断する。
2. 返ってきた指摘（日本語／論理／破綻／体裁）を**採否表**にする。意味が変わる言い換えや構成の組み替えは勝手に採用せず、依頼者に判断を委ねる。
3. 採用分を直し、`check_all.py` を再実行する。不採用は理由を1行残し、採否表を報告に添える。
4. **1ラリー終えたら止めて確認する**:「機械チェック → 目視QA → フレッシュアイ・レビュー → 採用分の修正 → 機械チェック再実行」を1ラリーとし、今回の指摘件数・採用件数・残る懸念を報告したうえで次ラリーを回すか確認する（依頼者が回数を事前指定している場合を除く）。

### 直し方・渡し方の作法

- 「QA済み」と報告するときは、実際に見たページ番号を書く（「全ページ」と書くのは全ページを見たときだけ）。
- 依頼者が手で直したページがあるときは、そのページを正として他のページを揃える。指示されていない箇所をついでに書き換えず、変えたほうがよい点は提案として添える。
- 社内用のページ（確認事項・社内向け付録）は `section` に `internal` クラスを付けて1つの HTML で管理する。社外に出すときは、PDF なら `body` に `external` クラスを付けて印刷し、PPTX なら `--external` を付けて書き出す。
- 渡すファイル名は NFC（濁点を結合した形）に揃える（`new_deck.py` と `html_to_pptx.py` は自動で NFC に揃える）。

## PowerPoint（.pptx）が要るとき

**資料は HTML で仕上げる。PPTX にするのは、依頼者が「PPTX で」「パワポにして」と明示したときだけ。** 修正・レビューの往復はすべて HTML 上で回し、変換は最後に1回だけ行う。PPTX を渡した後に修正が来たら、HTML を直して変換し直す。明示が無ければ PDF で渡す。

1. HTML で手順6〜9（機械チェック FAIL 0・目視 QA・フレッシュアイ・レビュー）まで済ませる
2. 変換: `uv run "<SKILL_DIR>/scripts/html_to_pptx.py" mydeck.html` → 同じフォルダに `mydeck.pptx` を出力
3. 検査: `uv run "<SKILL_DIR>/scripts/check_deck.py" mydeck.pptx` を FAIL 0 にし、`uv run "<SKILL_DIR>/scripts/check_deck_layout.py" mydeck.pptx` の WARN（隠れた文字・はみ出し・あふれ・画像の引き伸ばし・空の箱）と文字の折り返しを確かめる
4. 社外に送るならファイルのプロパティ（作成者・会社名など）を消し、社内用ページ（`section.internal`）は `--external` を付けて除く

### 既存の資料へ差し込むページ

差し込む先の PowerPoint 資料（社内の標準デッキ・作りかけの提案書）を渡されたときは、HTML から変換せず **その資料のマスターの上で直接組む**（slide-rules §8.7）。

1. 測る: `uv run "<SKILL_DIR>/scripts/measure_deck.py" house.pptx` → `house.skin.json`（`.potx` も可）。要約を読み、実物と照らして値を確かめる
2. 組む: [examples/house_deck_example.py](examples/house_deck_example.py) を手本に `scripts/deck_pptx.py` で組む（書体・地色・ページ番号をスライド側に直書きしない／タイトルはプレースホルダー／表は資料と同じ作り／色はテーマ色）
3. 検査: `uv run "<SKILL_DIR>/scripts/check_deck.py" pages.pptx --house house.skin.json` → FAIL 0（PowerPoint で開く前に必ず通す）
4. 全ページを目視し、差し込むページだけのファイルを渡す（元の資料へ貼り付ける際は「貼り付け先のテーマを使用」）
