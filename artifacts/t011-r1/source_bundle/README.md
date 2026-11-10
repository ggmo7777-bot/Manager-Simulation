# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**現在: T011-R1まで実装・検証済み（2026-11-08）。** 4クラブ・4監督・12選手で、育成、選考、2対2の試合、相互合意移籍、有限資金台帳と育成還元を接続している。学習はA（技能状態）とB（認識・信頼）。C（Policy重み学習）、加齢・引退・新人による世代交代は未実装。

T011の現状レビューで見つかった3点を修正した。

- 未記録の直接成長ボーナスを削除し、資金→設備費支出→練習効果を比較した。
- 保存/読込は全履歴の再演照合を行い、状態・記憶参照・取引・履歴の不整合を拒否する。
- 選手の移籍先Policyは、本人の認識と明示的に届いたオファーだけで判断する。

全132テスト、主/追加330run、修正要因分離120runを完了。4条件の84日連続と42+42日再開、および1000日+14日が一致した。ボーナス削除後も育成還元による設備・成長効果は残ったが、成長差は旧結果より小さくなった。

[現在の状態](docs/STATUS.md) · [T011-R1結果](docs/experiments/T011-R1-RESULTS.md) · [検証レビューと制約](docs/experiments/T011-R1-REVIEW.md)

## 依頼された10項目

| 成果物 | 資料 |
|---|---|
| 1. プロジェクト憲章 | [目的・優先順位・必須原則](docs/CHARTER.md) |
| 2. 未確定事項 | [候補・必要な検証・変更の影響](docs/OPEN-QUESTIONS.md) |
| 3. システム全体像 | [状態/観測/時間/試合/経済/ログ](docs/DESIGN.md) |
| 4. 個体の状態・行動・学習 | [選手/監督、A/B/C、Policy方式](docs/DESIGN.md) |
| 5. 最小実験環境 | [段階的な環境](docs/ROADMAP.md) |
| 6. 段階的ロードマップ | [開発順序と理由](docs/ROADMAP.md) |
| 7. 成功・失敗・比較実験 | [段階別ゲート](docs/ROADMAP.md)、[最新修正プロトコル](docs/experiments/T011-R1-PROTOCOL.md) |
| 8. 実装タスク | [最初のT001](docs/tasks/T001.md)、[今回のT011-R1](docs/tasks/T011-R1.md)、[Sol引き継ぎ](docs/SOL-HANDOFF.md) |
| 9. 設計変更・実験の管理 | [管理方法](docs/GOVERNANCE.md)、[判断ログ](docs/DECISIONS.md) |
| 10. 研究課題・延期項目 | [未解決課題](docs/OPEN-QUESTIONS.md)、[調査と一次資料](docs/RESEARCH.md) |

設計は最終仕様ではなく、検証によって変更する仮説である。

## 実行

Python 3.13以上。検証環境はmacOS arm64 / CPython 3.13.3。標準ライブラリのみで、GPUや有料APIは不要。

```sh
cd /Users/yamaguchitoshi/Documents/soccer
python3 -B -m unittest discover -s tests -v
python3 -B -m soccer_sim run --condition league_open_solidarity --seed 100 --days 42 --out artifacts/local/league-first42
python3 -B -m soccer_sim resume --snapshot artifacts/local/league-first42/snapshot.json --days 42 --out artifacts/local/league-resumed84
python3 -B -m soccer_sim experiment --suite t011 --out artifacts/local/t011-comparison
```

出力はsnapshot.json、events.jsonl、summary.json、manifest.json。真値ログは研究者向けで、Policyの入力には使わない。`--days`は追加日数。同じ出力先を指定するとファイルを更新するため、凍結成果物へ出力しない。

T011-R1はsave12 / rules v2 / policy v2。全再演を含む読込は1000日で約7.4秒。seed/configから再現できない記録外の途中介入は保存/再開できない。コードのsource hashが違う保存は拒否するため、旧snapshotは対応する旧source_bundleで読む。ソース指紋を手で書き換えて互換扱いにしない。

## 過去の結果と保管

T001〜T007で育成・認識・信頼、T008で最小2D競技、T009で出場機会、T010で2クラブ間の相互合意移籍を実装した。各段階の実測・著者記録・制約は [STATUS](docs/STATUS.md) と個別のRESULTSに保持している。T008〜T011旧版の原著者表記はAntigravity / Gemini 4 Argon、今回のT011-R1修正はCodex。

`artifacts/t001/`〜`artifacts/t011/`を比較証拠として保持し、最新の検証は`artifacts/t011-r1/`に置く。日常の開発先はルートの`soccer_sim/`。中間版の結果は最終版と分けて保存する。

移籍窓は日28/56の2回だけで、1000日試験は世代交代や毎季の市場の検証ではない。初期プロフィールも固定fixtureであり、現時点の結果を豊かな世界史の完成とは扱わない。次の作業は、その回に依頼された最小タスクだけを実施する。
