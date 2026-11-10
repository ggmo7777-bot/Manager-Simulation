# 現在の状態 — 2026-11-09

**T017のsampled集団CLI接続まで実装・検証・再現用保管を完了。** save16 / rules T015 / evaluation T016。4クラブ・4監督・12選手、A/B学習、日予算、2対2、選考、合意移籍、整数会計・育成還元、記録付き認識介入を持つ。最上位目的は独立個体の経験と選択から再現可能な歴史が生まれること。

## 最新の結果

- 通常runで7つのリーグ条件の`_sampled_population`接尾辞を使用可能。接尾辞なしのfixed、既定baseline、既存の実験条件集合を維持。
- 全196テスト合格（103.318秒）、高速50件（2.798秒）。14条件×seed100×35日のCLI実行がAPI/凍結T016と状態・履歴・集計一致。
- sampledの14→35日再開、記録付き信頼分岐→再開が一致し、親保存は不変。不正条件と旧source hashのsnapshotを出力作成前に拒否。
- 2,993取引と167要求を独立監査。旧1,911ファイル不変。package差分は__main__.pyだけ。新結果・再現用ソースは`artifacts/t017/`。
- T016の初期育成全員の比較と移籍なし集団の記述分離は維持。旧4名はlegacy診断。T016の180世界・初期育成6名の還元差（fixed +0.015301、sampled +0.007379）は過去の評価結果であり、今回多seed再分析は行っていない。

[T017結果](experiments/T017-RESULTS.md) / [契約と事前条件](tasks/T017.md) / [T016結果](experiments/T016-RESULTS.md)。

## 検証済みの基盤

T013は疲労測定/B更新、実請求・循環オファーの受付、主体ごとの移籍判断、介入の原因参照を修正。[結果](experiments/T013-RESULTS.md)。T014は整数通貨へ移行し、経済240run/分岐30系列/1000日で旧版と正規化後の全履歴が一致。[結果](experiments/T014-RESULTS.md)。T012以降は許可したB変数への研究介入を記録し分岐保存/再開できる。[結果](experiments/T012-RESULTS.md)。

T015は初期分布の範囲生成を追加。固定4名の旧「残留側成長」という解釈はT016で改訂した。旧数値はlegacy診断として保持。[旧結果と注意](experiments/T015-RESULTS.md)。

## 制約と次の小さな候補

1. 次の技術候補はruntime/CLI/分析の指紋範囲の分離。現在はCLI変更でも旧snapshotを拒否する。実行依存の見逃しと不要な拒否の両方を検査できる互換契約が必要。
2. 初期範囲は未校正。分布/初期印象/旧p3,p9設定の効果分解は未実施。T016のラベル付替え検査は評価器に限定し、世界全体の一般化とは呼ばない。
3. 保存・読込は全履歴再演。旧T014の1000日読込7.817秒。T016で1000日を再実測していない。記録外介入は保存不可。
4. runtime hashはpackage内の旧experimentも含む。同じsave16でもT015/T016/T017のsource hashが違う旧保存は対応する旧ソースで読む。評価版はsummary/manifestに記録。
5. 移籍窓は日28/56のみ。加齢・引退・新人・毎季市場・C・3D/UIは未実装。初期集団生成は世代交代ではない。
6. 世界間の重複、直書き係数、clamp/round6重複は残る。利息/税/分割配分の丸めも未定義。次候補の記載は一括着手指示ではない。

[外部14点の対応](reviews/2026-11-08-EXTERNAL-REVIEW.md)。

## 過去結果への索引

| 段階 | 主な範囲 / 結果 |
|---|---|
| T001–T003 | [基盤](experiments/T001-RESULTS.md) / [認識](experiments/T002-RESULTS.md) / [信頼](experiments/T003-RESULTS.md) |
| T004–T007 | [自己予測](experiments/T004-RESULTS.md) / [監督予測](experiments/T005-RESULTS.md) / [方法選択](experiments/T006-RESULTS.md) / [複数監督](experiments/T007-RESULTS.md) |
| 追加検証 | [旧STATUSの6改善表](experiments/T007-IMPROVEMENT-RESULTS.md) |
| T008–T010 | [2D競技](experiments/T008-RESULTS.md) / [出場機会](experiments/T009-RESULTS.md) / [合意移籍](experiments/T010-RESULTS.md) |
| T011/R1 | [元版](experiments/T011-RESULTS.md) / [成長/保存/選手境界の修正](experiments/T011-R1-RESULTS.md) |

T011/R1の旧「100%拒否」は実再送検査がなく、ガード有効性の根拠として撤回。疲労真値が入る旧結果を十分な認識分離の証拠にしない。凍結artifactは保持し、新結果で上書きしない。

運用: README → CHARTER → 本書 → 対象task/結果 → GOVERNANCE/DECISIONS/SOL-HANDOFF。通常の境界確認は`python3 -B tools/check.py --suite fast`、段階完了は全unittest。新runは既存ディレクトリへ上書きしない。Git未導入のため、`tools/artifact_inventory.py`でキャッシュ等を除外して凍結/照合する。
