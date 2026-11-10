# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**T019の初期分布の効果分解まで実装・検証済み（2026-11-10）。育成還元の効果の縮小（fixed→sampled）はプロフィールと初期印象にほぼ二分され、p3/p9満足度は寄与しない。**T018のリーグ実行指紋とCLI・分析来歴の分離を前提とする。 進行ルールはT015、保存形式はsave17、評価版は`t016-initial-cohort-v1`。4クラブ・4監督・12選手で、育成、選考、2対2、合意移籍、有限資金と育成還元を接続している。学習はA（技能状態）とB（認識・信頼）。C、加齢・引退・新人供給は未実装。

制度比較は初期育成6名を移籍後も追跡する。移籍しなかった選手は対象ID・人数を添えた記述統計に分ける。旧固定4名の集計は`legacy_four_slot_*`へ改名した。初期プロフィールは`fixed`（既定）または`sampled`で、生成範囲は引き続き未校正の仮説。

全218テスト合格（T019時点）。リーグのsource hashは12実行ファイルに限定し、CLI・分析の変更では保存の再開・分岐が拒否されない。14世界の35日実行は凍結T017と一致。cache後の再実行はPython 3.14.0で行った（基準3.13.3からの逸脱）。T017のsampled CLI接続（fixed/sampled各7条件）も維持。T016で検証した初期育成集団の制度比較と、未校正の生成範囲の制約は維持する。

[現在の状態](docs/STATUS.md) · [T019結果](docs/experiments/T019-RESULTS.md) · [T018結果](docs/experiments/T018-RESULTS.md) · [T017結果](docs/experiments/T017-RESULTS.md) · [T016結果](docs/experiments/T016-RESULTS.md) · [T015の旧評価問題](docs/reviews/2026-11-08-T015-STATUS-REVIEW.md) · [外部14点の対応](docs/reviews/2026-11-08-EXTERNAL-REVIEW.md)

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

現行リーグsave17は全再演方式。記録付きの監督技能推定/選手信頼介入だけを復元できる。無記録の変更は拒否する。リーグの実行ファイル12件のhash(`league-runtime-files-v1`)が違う保存、およびsave16以前は対応する旧source_bundleで読む。指紋を手で書き換えて互換扱いにしない。

7つのリーグ条件すべてで`_sampled_population`接尾辞を使用できる。接尾辞なしは従来のfixed条件。`resume`/`branch`はsnapshot内の集団設定を保持する。上の例は存在しない出力先で実行する。

CLI接続の比較検証（fixed/sampled各7条件、seed100、35日）は次のコマンド。既存出力先は拒否する。

```sh
python3 -B experiments/t017.py --out artifacts/local/new-cli-comparison
```

## 過去の結果と保管

T001〜T007で育成・認識・信頼、T008で最小2D競技、T009で出場機会、T010で2クラブ間の相互合意移籍を実装した。各段階の実測・著者記録・制約は [STATUS](docs/STATUS.md) と個別のRESULTSに保持している。T008〜T011旧版の原著者表記はAntigravity / Gemini 4 Argon、T011-R1/T013修正はCodex。T012/T014の実装はSol 6.1 High、設計・独立検証と記録はCodex。T012の利用上限後の補修もCodex。

`artifacts/t001/`〜`artifacts/t011/`を比較証拠として保持し、T014凍結成果物は`artifacts/t014/`、T015の旧試用結果は`artifacts/t015/run1/`、修正前ソースは`artifacts/t015/reference_before_t016/`。T016の結果・凍結ソースは`artifacts/t016/`、T017は`artifacts/t017/`、T018は`artifacts/t018/`。T013最終版は`artifacts/t013/final/`、T013中間結果も別に保持する。日常の開発先はルートの`soccer_sim/`。中間版の結果は最終版と分けて保存する。

移籍窓は日28/56の2回だけで、1000日試験は世代交代や毎季の市場の検証ではない。初期プロフィールは固定fixtureまたは未校正の範囲生成であり、現時点の結果を豊かな世界史の完成とは扱わない。次の作業は、その回に依頼された最小タスクだけを実施する。

旧T011/T011-R1には疲労真値漏洩と未実試行ガードが残っていた。旧拒否率をガード有効性の証拠にしない。直接readerは未実装。初期集団の範囲生成はT015で導入し、T016で評価を修正した。範囲の校正は未実施。

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
