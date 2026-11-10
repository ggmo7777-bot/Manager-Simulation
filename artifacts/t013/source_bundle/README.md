# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**現在: T012の分岐保存と、T013の観測・請求ガード修正を実装・検証済み（2026-11-08）。** 4クラブ・4監督・12選手で、育成、選考、2対2の試合、相互合意移籍、有限資金台帳と育成還元を接続している。学習はA（技能状態）とB（認識・信頼）。C、加齢・引退・新人供給は未実装。

外部レビューを受け、疲労を観測者ごとの測定から認識へ反映するよう修正した。配当の通常請求と重複再送は共通の受付を通り、要求・拒否・支払を記録する。監督の放出/獲得判断は各自のMindだけを受け取る。記録した認識介入から分岐世界を保存・再開できる。

全153テスト、経済240run、分岐200系列を検証。重複請求の実再送360件を拒否し、二重支払は0件。1000日分岐の保存・再開も一致した。

[現在の状態](docs/STATUS.md) · [T013結果](docs/experiments/T013-RESULTS.md) · [T012結果](docs/experiments/T012-RESULTS.md) · [外部14点の対応/未対応](docs/reviews/2026-11-08-EXTERNAL-REVIEW.md) · [T013契約](docs/tasks/T013.md) · [T012契約](docs/tasks/T012.md)

## 依頼された10項目

| 成果物 | 資料 |
|---|---|
| 1. プロジェクト憲章 | [目的・優先順位・必須原則](docs/CHARTER.md) |
| 2. 未確定事項 | [候補・必要な検証・変更の影響](docs/OPEN-QUESTIONS.md) |
| 3. システム全体像 | [状態/観測/時間/試合/経済/ログ](docs/DESIGN.md) |
| 4. 個体の状態・行動・学習 | [選手/監督、A/B/C、Policy方式](docs/DESIGN.md) |
| 5. 最小実験環境 | [段階的な環境](docs/ROADMAP.md) |
| 6. 段階的ロードマップ | [開発順序と理由](docs/ROADMAP.md) |
| 7. 成功・失敗・比較実験 | [段階別ゲート](docs/ROADMAP.md)、[最新修正プロトコル](docs/experiments/T013-PROTOCOL.md) |
| 8. 実装タスク | [最初のT001](docs/tasks/T001.md)、[今回のT013](docs/tasks/T013.md)、[Sol引き継ぎ](docs/SOL-HANDOFF.md) |
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

現行save14は全再演方式。記録付きの監督技能推定/選手信頼介入だけを復元できる。無記録の変更は拒否する。コードのsource hashが違う保存は対応する旧source_bundleで読む。指紋を手で書き換えて互換扱いにしない。

比較実験は新しい出力先で実行する（既存先は拒否）。

```sh
python3 -B experiments/t013.py --out artifacts/local/new-economy-comparison
python3 -B experiments/t012.py --out artifacts/local/new-branch-comparison
```

## 過去の結果と保管

T001〜T007で育成・認識・信頼、T008で最小2D競技、T009で出場機会、T010で2クラブ間の相互合意移籍を実装した。各段階の実測・著者記録・制約は [STATUS](docs/STATUS.md) と個別のRESULTSに保持している。T008〜T011旧版の原著者表記はAntigravity / Gemini 4 Argon、T011-R1/T013修正はCodex。T012の実装はSol 6.1 High、利用上限後の検証と補修はCodex。

`artifacts/t001/`〜`artifacts/t011/`を比較証拠として保持し、最新の検証は`artifacts/t013/final/`、初回の中間結果は`artifacts/t013/economy/`と`branches/`に置く。日常の開発先はルートの`soccer_sim/`。中間版の結果は最終版と分けて保存する。

移籍窓は日28/56の2回だけで、1000日試験は世代交代や毎季の市場の検証ではない。初期プロフィールも固定fixtureであり、現時点の結果を豊かな世界史の完成とは扱わない。次の作業は、その回に依頼された最小タスクだけを実施する。

旧T011/T011-R1には疲労真値漏洩と未実試行ガードが残っていた。旧拒否率をガード有効性の証拠にしない。通貨の整数化、直接reader、初期集団の分布生成は未実装。
