# T007 主担当の確認と判断

2026-11-06。Sol 6.1 Highが新機構・12件のテスト・240run比較を実装/実行後、利用上限で停止した。主担当が残る独立検証、結果分析、設計更新、凍結保存を完了した。全83テストを独立に再実行し合格した。[記録](../../artifacts/t007/review/tests.json)

## 確認範囲

- 新しいMultiWorldは選手の真値を一組だけ持ち、各CoachMindと相手別TrustEdgeを分離。使われないscalar trustを新選手Mindに残していない。
- 全監督の判断を行動実行前に計算する。私的信号は監督IDを乱数channelに含み、未払いの相手に観測や学習が伝わらない。費用を払った重複観察は認める。
- 得点のscore_sourceと、実施した方法に一致するfeedback_eligibleを区別。未受領・別方法・休息・他選手の経験への信頼更新を認めない。相手別評価は更新前の自己予測と相手自身の事前trustを使う。
- 代表seed17の監督168判断/選手168判断/26feedbackを、実装のPolicy・feedback関数を使わず、履歴と式から再計算した。108有料観察、50観察練習経験の所有者・根拠も一致。[独立監査](../../artifacts/t007/review/attribution-audit.json)
- 同方法二重評価の説明例seed0も同じ監査で一致。34feedbackのうち7件は得点の出所でない監督への評価だった。例の選定規則は[RESULTS](T007-RESULTS.md)に明示。[例の監査](../../artifacts/t007/review/attribution_example/attribution-audit.json)
- 4条件の84日vs42+42日がテストで一致。別途CLIの固定代表もsnapshot/JSONLのバイト一致。所有者違いの記憶、予測・原因・予算・設定・種別などの破損拒否を検査した。
- T006の240本と版以外の全snapshotが一致し、過去251ファイルのhash不変。[互換性検査](../../artifacts/t007/review/verification.json)
- 主比較と現行ソースの版（`source_sha256: 7bbfcefbb6e4f5b03f1315c3e44e1b17d1e1df1cdc4bb4c87d6cd0fcfb1ab2f7`）を照合。固定代表と説明例の行動/指標も主比較の該当run（[comparison.json](../../artifacts/t007/experiments/comparison.json) / [summary.json](../../artifacts/t007/experiments/summary.json)）に一致。
- T007時点の現行ソースはルートの `soccer_sim/` および `tests/` として保持し（新ファイル作成によるデータ混在を避けるため `artifacts/t007/source_bundle/` は別途複製せず）、現行T007と旧T006（[凍結ソース](../../artifacts/t006/source_bundle/manifest.json)）からそれぞれ対応するsnapshotを再開し一致を確認。全39資料のローカルリンク197件も検査しリンク切れ0件を確認した。

## 採用範囲と未解決

相手別の私的経験を保持する小さな研究用世界として採用する。旧1監督を全面置換せず、同じ意味の成長・観測・認識更新・監督Policyの関数を共用する。固定信頼と学習停止の対照を保持する。

save7 readerは型・範囲・所有者を検査したうえで全履歴を再演して照合する。専用validatorの重複を減らす設計であり、定数時間の状態復元ではない。実装前レビューでこの違いをD017とProtocolに記録した。長期化した場合の費用、途中介入、移行は別タスクにする。再演の自己一致だけを根拠にせず、上記の独立な式の照合を併用した。

受領予測を評価する標本は選手自身の選択で偏る。p2の多くのrunでは評価経験がなく、比較の欠損を隠していない。監督の学習で主比較の誤差が増えたこと、ノイズ0の対照では学習による行動差がなかったことも残した。これらを勝率や成長率の調整で消していない。

実装担当の報告だけを検証済みとせず、実行結果と保存物を照合してT007を完了とする（[凍結ソース](../../artifacts/t007/source_bundle/manifest.json)）。さらに2026-11-07、T008着手前に既存成果物とソース指紋を不変に保ったインメモリ対照実験により、① T002の監督提案固定による選手G直接効果（`-0.043244`）と監督反応の間接補償（`+0.002517`）の分離、② 残差学習率 $\alpha_{\text{res}}=0.10$ による監督予測MSE悪化の解消（既定 `0.50` 比 `c1 -0.000676`, `c2 -0.000527`）、③ 両予測負時の提案抑制ガード、④ 非負助言項 `max(0, trust - autonomy)` による `p2` 評価欠損の半減（feedback回数約5倍）、⑤ 初期観察位相1日差による重複観察の90.8%削減（`45.40 → 4.17` 日）、⑥ 1パス線形監査readerによる読込速度2.06〜3.15倍化と破損18/18拒否を追加確認した（詳細は[RESULTS](T007-RESULTS.md)および[STATUS](../STATUS.md)）。その後、別作成者（Antigravity / Gemini 4 Argon）により[T008](../tasks/T008.md)（[結果](T008-RESULTS.md) / [レビュー](T008-REVIEW.md)）が実装・検証された。
