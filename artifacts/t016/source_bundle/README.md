# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**T016の評価指標修正まで実装・検証済み（最終確認2026-11-09）。** 進行ルールはT015、保存形式はsave16、評価版は`t016-initial-cohort-v1`。4クラブ・4監督・12選手で、育成、選考、2対2、合意移籍、有限資金と育成還元を接続している。学習はA（技能状態）とB（認識・信頼）。C、加齢・引退・新人供給は未実装。

制度比較は初期育成6名を移籍後も追跡する。移籍しなかった選手は対象ID・人数を添えた記述統計に分ける。旧固定4名の集計は`legacy_four_slot_*`へ改名した。初期プロフィールは`fixed`（既定）または`sampled`で、生成範囲は引き続き未校正の仮説。

全190テスト合格。180世界で旧T015と全状態・全履歴が一致し、107,376取引も独立監査した。初期育成6名への還元制度の平均成長差はfixed +0.015301、sampled +0.007379。30seedの再分析であり、現実の育成や残留者だけへの効果を証明した値ではない。

[現在の状態](docs/STATUS.md) · [T016結果](docs/experiments/T016-RESULTS.md) · [T015の旧評価問題](docs/reviews/2026-11-08-T015-STATUS-REVIEW.md) · [外部14点の対応](docs/reviews/2026-11-08-EXTERNAL-REVIEW.md)

## 依頼された10項目

| 成果物 | 資料 |
|---|---|
| 1. プロジェクト憲章 | [目的・優先順位・必須原則](docs/CHARTER.md) |
| 2. 未確定事項 | [候補・必要な検証・変更の影響](docs/OPEN-QUESTIONS.md) |
| 3. システム全体像 | [状態/観測/時間/試合/経済/ログ](docs/DESIGN.md) |
| 4. 個体の状態・行動・学習 | [選手/監督、A/B/C、Policy方式](docs/DESIGN.md) |
| 5. 最小実験環境 | [段階的な環境](docs/ROADMAP.md) |
| 6. 段階的ロードマップ | [開発順序と理由](docs/ROADMAP.md) |
| 7. 成功・失敗・比較実験 | [段階別ゲート](docs/ROADMAP.md)、[T016の検証契約](docs/tasks/T016.md) |
| 8. 実装タスク | [最初のT001](docs/tasks/T001.md)、[今回のT016](docs/tasks/T016.md)、[Sol引き継ぎ](docs/SOL-HANDOFF.md) |
| 9. 設計変更・実験の管理 | [管理方法](docs/GOVERNANCE.md)、[判断ログ](docs/DECISIONS.md) |
| 10. 研究課題・延期項目 | [未解決課題](docs/OPEN-QUESTIONS.md)、[調査と一次資料](docs/RESEARCH.md) |

設計は最終仕様ではなく、検証によって変更する仮説である。

## 実行

Python 3.13以上。検証環境はmacOS arm64 / CPython 3.13.3。標準ライブラリのみで、GPUや有料APIは不要。

```sh
cd /Users/yamaguchitoshi/Documents/soccer
python3 -B tools/check.py --suite fast
python3 -B -m unittest discover -s tests -v
python3 -B -m soccer_sim run --condition league_open_solidarity --seed 100 --days 14 --out artifacts/local/parent14
python3 -B -m soccer_sim branch --snapshot artifacts/local/parent14/snapshot.json --kind player_trust --player p3 --coach c1 --value 0.05 --reason "信頼差の比較" --days 28 --out artifacts/local/trust-low42
python3 -B -m soccer_sim resume --snapshot artifacts/local/trust-low42/snapshot.json --days 42 --out artifacts/local/trust-low84
```

出力はsnapshot.json、events.jsonl、summary.json、manifest.json。真値ログと研究介入は研究者向けで、Policyの入力ではない。`--days`は追加日数。日常CLIは同じ出力先を更新するため、凍結フォルダへ出力しない。branchは親の出力ディレクトリを拒否する。

現行save16は全再演方式。記録付きの監督技能推定/選手信頼介入だけを復元できる。無記録の変更は拒否する。コードのsource hashが違う保存は対応する旧source_bundleで読む。指紋を手で書き換えて互換扱いにしない。

以下のrun CLIはfixed条件。通常CLIのsampled起動は未接続で、fixed/sampledの比較実験は専用スクリプトから新しい出力先で実行する（既存先は拒否）。

```sh
python3 -B experiments/t016.py --out artifacts/local/new-cohort-comparison
```

## 過去の結果と保管

T001〜T007で育成・認識・信頼、T008で最小2D競技、T009で出場機会、T010で2クラブ間の相互合意移籍を実装した。各段階の実測・著者記録・制約は [STATUS](docs/STATUS.md) と個別のRESULTSに保持している。T008〜T011旧版の原著者表記はAntigravity / Gemini 4 Argon、T011-R1/T013修正はCodex。T012/T014の実装はSol 6.1 High、設計・独立検証と記録はCodex。T012の利用上限後の補修もCodex。

`artifacts/t001/`〜`artifacts/t011/`を比較証拠として保持し、T014凍結成果物は`artifacts/t014/`、T015の旧試用結果は`artifacts/t015/run1/`、修正前ソースは`artifacts/t015/reference_before_t016/`。T016の結果・凍結ソースは`artifacts/t016/`。T013最終版は`artifacts/t013/final/`、T013中間結果も別に保持する。日常の開発先はルートの`soccer_sim/`。中間版の結果は最終版と分けて保存する。

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
