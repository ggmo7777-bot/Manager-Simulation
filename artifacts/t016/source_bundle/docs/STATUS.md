# 現在の状態 — 2026-11-09

**T016の評価修正まで実装・検証・再現用保管を完了。** 現行はsave16 / rules T015 / evaluation `t016-initial-cohort-v1`。最上位目的は独立個体の経験と選択から再現可能な歴史が生まれること。4クラブ・4監督・12選手、A/B学習、日予算、2対2、選考、合意移籍、整数会計・育成還元、記録付き認識介入が現在の範囲。

## 最新の結果

- 初期育成6名を所属の初期状態から選び、移籍後も追跡する主指標を追加。移籍回数0かつ初期クラブにいる者は別の記述統計。ID/人数を保存し、空平均はnull。
- 誤解を招いた`academy_retained_skill_gain`を削除し、旧4名は`legacy_four_slot_*`へ明示改名。既存結果を上書きしない。
- 全190テスト合格（97.032秒）、高速48件（2.698秒）。180世界で旧T015とidentity以外の全状態・全履歴が一致し、旧8集計も明示改名後に再現。
- 初期/最終状態と移籍イベントから集団を独立照合。107,376取引を整数監査。4代表42+42日とsampledの信頼分岐で保存・再開が一致。
- 初期育成6名の還元制度の成長差: fixed +0.015301、sampled +0.007379（29正/0負/1ゼロ）。制度パッケージの総効果であり、残留者のみへの効果ではない。30seedの探索的再分析。
- 旧1,702ファイル不変。修正前T015は`artifacts/t015/reference_before_t016/`、新結果・ソースは`artifacts/t016/`に保管。T015の生成範囲の校正/一般化は未確定。

[T016結果](experiments/T016-RESULTS.md) / [契約](tasks/T016.md) / [条件](experiments/T016-PROTOCOL.md) / [発端のレビュー](reviews/2026-11-08-T015-STATUS-REVIEW.md)。

## 検証済みの基盤

T013は疲労測定/B更新、実請求・循環オファーの受付、主体ごとの移籍判断、介入の原因参照を修正。[結果](experiments/T013-RESULTS.md)。T014は整数通貨へ移行し、経済240run/分岐30系列/1000日で旧版と正規化後の全履歴が一致。[結果](experiments/T014-RESULTS.md)。T012以降は許可したB変数への研究介入を記録し分岐保存/再開できる。[結果](experiments/T012-RESULTS.md)。

T015は初期分布の範囲生成を追加。固定4名の旧「残留側成長」という解釈はT016で改訂した。旧数値はlegacy診断として保持。[旧結果と注意](experiments/T015-RESULTS.md)。

## 制約と次の小さな候補

1. 通常CLIのsampled起動は未接続。Python APIとT016実験で実行できる。起動条件の接続は小さな次候補。
2. 初期範囲は未校正。分布/初期印象/旧p3,p9設定の効果分解は未実施。T016のラベル付替え検査は評価器に限定し、世界全体の一般化とは呼ばない。
3. 保存・読込は全履歴再演。旧T014の1000日読込7.817秒。T016で1000日を再実測していない。記録外介入は保存不可。
4. runtime hashはpackage内の旧experimentも含む。同じsave16でもT015/T016のsource hashが違う旧保存は対応する旧ソースで読む。評価版はsummary/manifestに記録。
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
