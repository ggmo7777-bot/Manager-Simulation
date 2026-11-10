# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**T032で、T031が残した「観察100%でも先発しない」仮説(練習日の観察が試合評価推定を更新しないため)を検証し、仮説は反証された。`coach_practice_rating`(既定"none"、"blend_v1"。有効時は練習日の観察が監督の試合評価推定を偵察と同じ0.70/0.30で更新。B層のみ、真値・乱数なし、既定は不変)を追加。新人の試合評価推定誤差は減る(0.0894→0.0858、P1支持)が、先発した新人の割合は64.0%→61.9%に減り(P2反証、+3pt以上の予測)、真に上位2人の新人の先発も74.0%→70.7%(P3反証)、見逃しは143→163人に増えた。費用は事前登録の線を超えず(技能+0.0003、信頼−0.0007、先発の真の技能+0.0020)、満足度+0.031・先発の入れ替わり0.219→0.285が有意に増加(原因未分解)。** 

**T031で控え若手の発見の最小機構(`coach_exploration`、既定"none"。有効時は監督の観察候補の未観察日数の項の上限が10日から40日になり、入力は監督のB層の`last_seen_day`のみ。真値・乱数なし、既定は不変)を追加。新人の観察率は64.4%→100%(P1支持)、推定誤差は小さく減る(P3支持)が、先発した新人の割合は64.4%→64.0%で変わらず(P2反証)。費用は事前登録の線(技能−0.005・信頼−0.010)を超えず、技能−0.0016(有意でない)、信頼+0.0049・先発の技能+0.0024・満足度+0.013はむしろ有意に増加。**

**T030で世代交代の最初の最小単位(`generations`、既定"none"。有効時は年度末に、本人の年齢と出場満足度だけで引退を決め、同日に3人未満のクラブへ18歳の新人を別乱数チャネルで受け入れ、理由つきイベントで記録。新人の監督の第一印象は定数で真値は見えない)を追加。T029で監督の受容度の信念(`coach_belief`、既定"none"。有効時は監督が`coach_advice_outcome`だけから選手ごとの受容度を推定してB層に持ち、`use_v1`のみ提案候補の価値に反映。真の特性は見えない)を追加。T028で助言への態度(`advice_response`、既定"none"。有効時は選手が本人の状態と特性から 従う/部分従順/拒否 を決め、理由を記録。監督は結果だけ見る)を追加。T027で性格の潜在特性(`personality`、既定"none"。有効時は選手の真の状態として`discipline`・`emotional_stability`を保持するだけで、どの判断にも使わずPolicyにも見せない)を追加。T026で年齢の効果(`aging_curve`、既定"none"。有効時は年度末の記録イベント`season_skill_aged`が全選手の技能を未校正の仮説曲線で成熟・衰えさせる。Policyは年齢を見ない)を追加。T025で加齢(`aging_start_age`、既定0=無効。有効時は年度末に年齢+1を記録イベント`season_aged`として残す。年齢カウンタのみで技能・移籍等には作用しない)を追加。T024で年度ごとの順位表(`league_standings`、イベントだけから導く純関数。進行・保存は不変、表示・昇降格は無し)を追加。T023で年度区切り(`season_length_days`、既定0=無効、進行・履歴は不変。加齢・引退・新人・順位表はまだ無し)を追加(M6の最初の単位)。T022でリーグ保存の信頼manifest付き直接reader(`load_league_snapshot_trusted`、読込のみ約4〜6倍、認証は既定の全再演)を追加。T021でT020の拒否3摂動を再実行し、初期範囲の感度掃引（2026-11-10、範囲は不変）が19セルで完了。育成還元の効果に最も効くのは成長率の中心で、上限・忠誠を除く心理の幅は検出可能差以下。実データによる校正は未着手。**T019では育成還元の効果の縮小（fixed→sampled）がプロフィールと初期印象にほぼ二分され、p3/p9満足度は寄与しないと分解した。T018のリーグ実行指紋とCLI・分析来歴の分離を前提とする。 進行ルールはT015、保存形式はsave17、評価版は`t016-initial-cohort-v1`。4クラブ・4監督・12選手で、育成、選考、2対2、合意移籍、有限資金と育成還元を接続している。学習はA（技能状態）とB（認識・信頼）。C、加齢・引退・新人供給は未実装。

制度比較は初期育成6名を移籍後も追跡する。移籍しなかった選手は対象ID・人数を添えた記述統計に分ける。旧固定4名の集計は`legacy_four_slot_*`へ改名した。初期プロフィールは`fixed`（既定）または`sampled`で、生成範囲は引き続き未校正の仮説。

全284テスト合格（T032時点）。リーグのsource hashは12実行ファイルに限定し、CLI・分析の変更では保存の再開・分岐が拒否されない。14世界の35日実行は凍結T017と一致。cache後の再実行はPython 3.14.0で行った（基準3.13.3からの逸脱）。T017のsampled CLI接続（fixed/sampled各7条件）も維持。T016で検証した初期育成集団の制度比較と、未校正の生成範囲の制約は維持する。

[現在の状態](docs/STATUS.md) · [T032結果](docs/experiments/T032-RESULTS.md) · [T031結果](docs/experiments/T031-RESULTS.md) · [T030結果](docs/experiments/T030-RESULTS.md) · [T029結果](docs/experiments/T029-RESULTS.md) · [T028結果](docs/experiments/T028-RESULTS.md) · [T027結果](docs/experiments/T027-RESULTS.md) · [T026結果](docs/experiments/T026-RESULTS.md) · [T025結果](docs/experiments/T025-RESULTS.md) · [T024結果](docs/experiments/T024-RESULTS.md) · [T023結果](docs/experiments/T023-RESULTS.md) · [T022結果](docs/experiments/T022-RESULTS.md) · [T021結果](docs/experiments/T021-RESULTS.md) · [T020結果](docs/experiments/T020-RESULTS.md) · [T019結果](docs/experiments/T019-RESULTS.md) · [T018結果](docs/experiments/T018-RESULTS.md) · [T017結果](docs/experiments/T017-RESULTS.md) · [T016結果](docs/experiments/T016-RESULTS.md) · [T015の旧評価問題](docs/reviews/2026-11-08-T015-STATUS-REVIEW.md) · [外部14点の対応](docs/reviews/2026-11-08-EXTERNAL-REVIEW.md)

## 依頼された10項目

| 成果物 | 資料 |
|---|---|
| 1. プロジェクト憲章 | [目的・優先順位・必須原則](docs/CHARTER.md) |
| 2. 未確定事項 | [候補・必要な検証・変更の影響](docs/OPEN-QUESTIONS.md) |
| 3. システム全体像 | [状態/観測/時間/試合/経済/ログ](docs/DESIGN.md) |
| 4. 個体の状態・行動・学習 | [選手/監督、A/B/C、Policy方式](docs/DESIGN.md) |
| 5. 最小実験環境 | [段階的な環境](docs/ROADMAP.md) |
| 6. 段階的ロードマップ | [開発順序と理由](docs/ROADMAP.md) |
| 7. 成功・失敗・比較実験 | [段階別ゲート](docs/ROADMAP.md)、[T018の検証契約](docs/tasks/T018.md) |
| 8. 実装タスク | [最初のT001](docs/tasks/T001.md)、[今回のT018](docs/tasks/T018.md)、[Sol引き継ぎ](docs/SOL-HANDOFF.md) |
| 9. 設計変更・実験の管理 | [管理方法](docs/GOVERNANCE.md)、[判断ログ](docs/DECISIONS.md) |
| 10. 研究課題・延期項目 | [未解決課題](docs/OPEN-QUESTIONS.md)、[調査と一次資料](docs/RESEARCH.md) |

設計は最終仕様ではなく、検証によって変更する仮説である。

## AIエージェント・共同作業時の事前宣言ルール

複数のAIエージェント／作業者が並行して開発を進めるため、**作業・実装を開始する前に必ず以下の手順で作業予定をGitHub上へ明記すること**（AIは着手順序としてこれを自動的に確認・実行する）：

1. **最新の同期と進行中タスクの確認**
   - 作業前に `git fetch origin` で最新の `main` を確認し、あわせて **GitHub Issues（`open`）** と **Pull Requests（`open`）** を確認する（`gh issue list` / `gh pr list` 等）。
   - 他者・他AIがすでに着手中のタスク番号（`Txxx`）や変更予定ファイルと重複しないこと、前提となるスタックPRがないかを確認する。
2. **作業開始前の予定明記（Issue ＋ Draft PR）**
   - 実装や実験を始める前に、まず **GitHub Issue**（または作業用ブランチでの計画登録コミット＋**Draft Pull Request**）を作成し、以下を明記してからコード変更に着手する：
     - **タスク名・目的**（例: `T021: ...` / 独立実験）
     - **変更予定ファイル・影響範囲**（特に `soccer_sim/` の実行指紋12ファイルに触れるか否か）
     - **ベースブランチ・依存関係**（`main` 起点か、既存PRの上にスタックするか）
3. **ブランチ運用と本筋の分離**
   - `main` ブランチへの直接 `git push` は禁止。必ずタスク別の作業ブランチ（例: `t021-...`、`exp/...`）を切り、Pull Request 経由でマージする。
   - 本筋（`soccer_sim/`・公式タスク）と切り分けた探索的実験は `experiments/` 配下の専用ディレクトリ等へ隔離し、既存の凍結成果物（`artifacts/t001/`〜）や他者の作業領域を上書きしない。

## 実行

Python 3.13以上。検証環境はmacOS arm64 / CPython 3.13.3。標準ライブラリのみで、GPUや有料APIは不要。

```sh
cd /Users/yamaguchitoshi/Documents/soccer
python3 -B tools/check.py --suite fast
python3 -B -m unittest discover -s tests -v
python3 -B -m soccer_sim run --condition league_open_solidarity_sampled_population --seed 100 --days 14 --out artifacts/local/t017-sampled14
python3 -B -m soccer_sim resume --snapshot artifacts/local/t017-sampled14/snapshot.json --days 21 --out artifacts/local/t017-sampled35
python3 -B -m soccer_sim branch --snapshot artifacts/local/t017-sampled14/snapshot.json --kind player_trust --player p3 --coach c1 --value 0.05 --reason "信頼差の比較" --days 21 --out artifacts/local/t017-trust-low35
```

出力はsnapshot.json、events.jsonl、summary.json、manifest.json。真値ログと研究介入は研究者向けで、Policyの入力ではない。`--days`は追加日数。日常CLIは同じ出力先を更新するため、凍結フォルダへ出力しない。branchは親の出力ディレクトリを拒否する。

現行リーグsave17の既定は全再演方式(T022で、保存時検証済みmanifestに束縛した高速な`load_league_snapshot_trusted`を追加)。記録付きの監督技能推定/選手信頼介入だけを復元できる。無記録の変更は拒否する。リーグの実行ファイル12件のhash(`league-runtime-files-v1`)が違う保存、およびsave16以前は対応する旧source_bundleで読む。指紋を手で書き換えて互換扱いにしない。

7つのリーグ条件すべてで`_sampled_population`接尾辞を使用できる。接尾辞なしは従来のfixed条件。`resume`/`branch`はsnapshot内の集団設定を保持する。上の例は存在しない出力先で実行する。

CLI接続の比較検証（fixed/sampled各7条件、seed100、35日）は次のコマンド。既存出力先は拒否する。

```sh
python3 -B experiments/t017.py --out artifacts/local/new-cli-comparison
```

## 過去の結果と保管

T001〜T007で育成・認識・信頼、T008で最小2D競技、T009で出場機会、T010で2クラブ間の相互合意移籍を実装した。各段階の実測・著者記録・制約は [STATUS](docs/STATUS.md) と個別のRESULTSに保持している。T008〜T011旧版の原著者表記はAntigravity / Gemini 4 Argon、T011-R1/T013修正はCodex。T012/T014の実装はSol 6.1 High、設計・独立検証と記録はCodex。T012の利用上限後の補修もCodex。

`artifacts/t001/`〜`artifacts/t011/`を比較証拠として保持し、T014凍結成果物は`artifacts/t014/`、T015の旧試用結果は`artifacts/t015/run1/`、修正前ソースは`artifacts/t015/reference_before_t016/`。T016の結果・凍結ソースは`artifacts/t016/`、T017は`artifacts/t017/`、T018は`artifacts/t018/`。T013最終版は`artifacts/t013/final/`、T013中間結果も別に保持する。日常の開発先はルートの`soccer_sim/`。中間版の結果は最終版と分けて保存する。

移籍窓は日28/56の2回だけで、1000日試験は世代交代や毎季の市場の検証ではない。初期プロフィールは固定fixtureまたは未校正の範囲生成であり、現時点の結果を豊かな世界史の完成とは扱わない。次の作業は、その回に依頼された最小タスクだけを実施する。

旧T011/T011-R1には疲労真値漏洩と未実試行ガードが残っていた。旧拒否率をガード有効性の証拠にしない。直接reader(信頼manifest付き、読込のみ)はT022で追加、保存・forkは全再演のまま。初期集団の範囲生成はT015で導入し、T016で評価を修正した。範囲の校正は未実施。

金額APIの例（無次元の技能・疲労・倍率は従来どおり）:

```python
from dataclasses import replace
from soccer_sim.league_economy import LeagueEconomyConfig
from soccer_sim.money import money_units, format_money

config = replace(LeagueEconomyConfig(), facility_daily_cost_units=money_units("0.28"))
assert config.facility_daily_cost_units == 280000
assert format_money(280000) == "0.28"
```

`*_units`にfloatやboolは渡せない。最小単位未満は暗黙に丸めず拒否する。旧save14は凍結T013、旧save15は凍結T014ソースで読む。旧T012/T013実験スクリプトはsave14専用、T014スクリプトはsave15専用の案内を表示する。

T015修正前も現行もsave16だが、source hashが異なるため保存の読替えはしない。旧T015ソースは`artifacts/t015/reference_before_t016/source_bundle`を使う。summaryの評価版は状態schemaの版とは別に記録する。

初期育成集団の180世界比較は`python3 -B experiments/t016.py --out <新規出力先>`。T016以前のsave16保存は現行readerで拒否する。継続する場合は対応する凍結ソースを使う。
