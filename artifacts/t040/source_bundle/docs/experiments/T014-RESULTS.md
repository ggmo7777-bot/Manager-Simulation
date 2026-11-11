# T014結果 — 通貨計算・保存の整数化

2026-11-08。実装Sol 6.1 High、設計・独立監査・比較・記録Codex。
現行 `save15 / rules t014-integer-money-v1 / money integer-micro-v1`。PolicyはT013のまま。
source hash: `18bdefbba81f1b741725b64b8fd08f9f6f37752f410bc0bc4c3d292e4a1b5747`。

## 採用した変更

料金Config、契約、残高、賃金、累計、取引イベント、snapshot、summaryの通貨合計を、100万分の1単位のPython intへ統一した。名前は`*_units`。台帳の加減算・予算判定・保存則からround6/epsilonを除き、1単位の差もエラーとして検出する。

金額APIは非負intのみを受け取り、bool/float/文字列の自動変換を拒否する。人向けの変換は`money_units("0.28") == 280000`のように十進文字列を入力する。非ゼロの最小単位未満は丸めず拒否する。`format_money`は非負金額の表示用文字列を作る。倍率・能力・認識は無次元のfloatのまま。summaryの金額差は符号付きintになりうる。

旧save14を読み替える移行処理は作っていない。旧snapshotは旧凍結ソースで読む。判断・育成・試合・介入に関する11関数は構文木でも旧版と一致した。

## 実測

| 検証 | 結果 |
|---|---|
| 全自動テスト | 168件合格、93.233秒 |
| 新しい通貨テスト | 15件。1単位境界、反復送金、2^53超、無効型/端数、保存改変、配当実再送など |
| 高速ゲート | 33件合格、0.721秒 |
| 経済比較 | T013の7条件×30seed＋完全疲労測定対照30seed＝240run、全状態・全履歴が一致 |
| 分岐比較 | 5系列×6seed＝30系列、全状態・全履歴が一致 |
| 独立会計監査 | 経済240runの137,302取引で、各時点の保存則・口座・費目別累計が整数で一致 |
| 実受付監査 | 正規支払1,732件、重複再送拒否360件、循環拒否73件。二重支払なし。攻撃条件に限る循環拒否は60件 |
| 負の監査 | 取引の1単位変更、float混入、二重支払の挿入を独立監査が拒否。runtimeも1単位の保存則違反を拒否 |
| 保存再開 | 4主条件84日vs42+42日、5分岐day42→84日がsnapshot/JSONLバイト一致 |
| 長期 | 1000日分岐もT013と全履歴一致。保存再開+14日、再介入/同日介入+7日が一致 |
| 旧成果物 | 1,311ファイルのhashが開始時と一致 |

旧金額は監査側で独立に整数へ換算して比較した。除外はidentityと介入のparent_checkpoint_sha256のみ。親hashは版と保存表現が異なるため変わるが、介入値/順序/理由・全event ID・cause_refsは比較に含めた。旧経済240runを凍結版で再演したsummaryは、保存済みT013の値とも一致している。

通常条件で意図しないキャリア・成長・移籍・制度効果の変化は見つからなかった。これらは既知の固定集団と既存ルールでの技術的移行の検証で、経済モデルや初期集団の一般化の証明ではない。

1000日分岐の進行5.456秒、保存10.310秒、読込7.817秒、snapshot72,331,966 bytes。全比較プロセス434.410秒、peak RSS約922.1MiB。旧版と新版の比較・監査・複数世界を含む実測であり、単独性能ベンチマークではない。全再演readerは今回高速化していない。

## 再実行と証拠

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tools/check.py --suite fast
python3 -B experiments/t014.py --out artifacts/local/t014-new-comparison
```

[最終監査](../../artifacts/t014/review/final-verification.json) / [比較集計](../../artifacts/t014/comparison/summary.json) / [経済240本](../../artifacts/t014/comparison/economy.json) / [分岐30本](../../artifacts/t014/comparison/branches.json) / [全テストログ](../../artifacts/t014/review/tests-sol.txt) / [実行manifest](../../artifacts/t014/manifest.json) / [凍結source](../../artifacts/t014/source_bundle/manifest.json)。

T012/T013の旧実験スクリプトにはsave14の案内ガードを入れ、旧プロトコルのまま新単位を誤解釈して出力しないようにした。新しい移行比較はT014スクリプトを使う。旧成果物内の実行コードは変更していない。

## 残る課題

runtime/分析コードの指紋分離、全再演readerの高速化、固定初期集団からの分布生成、旧3世界の共通部分/直書き係数整理は未対応。利率・税・分割支払を追加するときの丸め/残余配分も未決定。世代交代・毎季市場・C・3D/UIは未実装。今回の完了を世界全体の完成としない。
