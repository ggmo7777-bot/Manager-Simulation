# 現在の状態 — 2026-11-08

**現行コードはT015（save16、未凍結・試用）。凍結済みの比較基準はT014（save15）。** 最上位目的は独立個体の経験と選択から再現可能な歴史が生まれること。4クラブ・4監督・12選手、A/B学習、日予算、2対2、選考、合意移籍、整数通貨の有限台帳・育成還元、記録付き認識介入が現在の範囲。

## 最新の確認

- 現行コードの全175テスト合格（94.784秒）。T014凍結252ファイル、source_bundle123ファイル、それ以前の1,311ファイルをhash照合し不一致なし。
- fixedの35比較でT014と全状態・全履歴一致。180実験の保存済み8集計値を再現し、107,376取引を独立監査した。
- T015の`sampled`は初期プロフィールと監督の印象を範囲から生成する。既定の`fixed`を維持する。範囲は未校正。T015の保存済みrun1と現在のruntime hashは一致。
- **T015の評価解釈に要修正。** `academy_retained_skill_gain`はp1,p2,p7,p8の固定4名を集計する。sampledの市場開放各条件30件中27件で移籍済みの選手が含まれ、+0.006886を実際の残留者への効果と呼べない。旧値は固定スロット指標として保持する。
- sampledの通常run CLIは未接続。Python APIと`experiments/t015.py`では実行できる。T015の採用・凍結は今回行っていない。
- README/引継ぎに残った旧save版・完了項目の案内を更新。今回シミュレーターの実行規則は変更していない。

[現状レビューと証拠](reviews/2026-11-08-T015-STATUS-REVIEW.md) / [T015契約](tasks/T015.md) / [T015結果と注意点](experiments/T015-RESULTS.md)。

## 検証済みの基盤

T013は疲労の測定/B更新を分離し、実際の重複請求・循環オファーを共通受付で拒否する。移籍の希望/放出/獲得は主体ごとのMindで判断する。介入の原因参照も保持する。[T013結果](experiments/T013-RESULTS.md)。

T014は通貨を1/1,000,000単位の整数で計算・保存。T013との経済240run/分岐30系列/1000日が単位正規化後に全状態・全履歴一致。168テストはT014時点の件数。現行T015の試用結果と混同しない。[T014結果](experiments/T014-RESULTS.md)。

T012以降は日境界の監督技能推定/選手信頼への研究介入を記録し、親を変えず分岐保存・再開できる。[T012結果](experiments/T012-RESULTS.md)。

## 制約と次の最小候補

1. 最優先候補はT015の評価対象の定義。初期育成6名の固定比較と、実際の残留者の記述統計を分ける。条件ごとに残留者を選び直すだけでは比較集団が変わる。別runで再集計し、旧結果を上書きしない。
2. 初期分布は仮説。固定fixtureより初期水準の散らばりが小さく、範囲/初期印象/旧p3,p9設定の効果を分解していない。ラベル置換も未実施。
3. 読込・保存は全履歴再演。T014の1000日読込は7.817秒。記録外の変更は保存不可。直接readerの旧実測を現行leagueの正しさ保証に流用しない。
4. runtime指紋はpackage内の旧experimentも含む。新しい分析は`experiments/`へ分離。旧snapshotは凍結sourceで読む。T015には凍結source_bundle/manifest/inventoryがまだない。
5. 移籍窓は日28/56のみ。加齢・引退・新人・毎季市場・C・3D/UIは未実装。1000日安定は自律進化する世界全体の妥当性ではない。
6. 世界間の実装重複、直書き係数、clamp/round6重複は残る。整数通貨の将来の利息/税/分割配分も未定義。次候補の記載は一括着手指示ではない。

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
