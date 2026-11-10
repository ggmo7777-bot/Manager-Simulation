# 現在の状態 — 2026-11-08

**T012分岐保存・T013観測/請求修正の実装と検証を完了。** 最上位目的は、独立個体の経験と選択から再現可能な歴史が生まれること。4クラブ/4監督/12選手、A/B学習、日予算、2対2、選考、合意移籍、有限資金/育成還元が現在の範囲。

## 今回の変更

- 疲労真値→認識の直結を廃止。自己/練習/スカウト/試合の測定とB更新を分離。未観測の控え監督評価は保持する。
- 配当の通常請求と再送を共通受付で検証。実請求・決済・拒否を記録。循環オファーも実受付を通す。放出/獲得Policyは各監督のMindだけを受け取る。
- T012は日境界の監督技能推定/選手信頼の研究介入を記録し、親を変更せず分岐保存/再開できる。save14、全再演方式。
- 全153テスト、経済240run、分岐200系列を検証。重複再送360件/循環オファー60件を実拒否、二重支払0。4主条件84日vs42+42日と5分岐系列、1000日+14日および連鎖介入の再開が一致した。
- 初回実験で信頼の根拠が疲労観測で途切れた5例を補修。再実験440本の数値/意味投影は初回と一致し、行動差の全例で介入への参照経路を確認した。グラフ上の経路は因果効果の単独の証明ではない。
- 育成還元による残留側成長差はR1の+0.032385から今回+0.022951へ縮小。固定集団上の制度パッケージ比較。1000日読込7.451秒、snapshot72.25MB、全実験プロセスのpeak RSS約1036.6MiB。全再演費用は残る。
- 旧成果物808ファイルのhashは不変。最終runは`artifacts/t013/final/`、初回結果は別に保持。

[T013結果](experiments/T013-RESULTS.md) / [T012結果](experiments/T012-RESULTS.md) / [最終検証](../artifacts/t013/review/final-verification.json) / [14点の対応表](reviews/2026-11-08-EXTERNAL-REVIEW.md) / [T013契約](tasks/T013.md) / [T012契約](tasks/T012.md)。次候補の記載は着手指示ではない。

## 制約と次候補

1. 本体通貨はfloat。整数micro-unitの監査で照合しているが、型自体の整数化は未実装。次の小さな正しさ改善候補。
2. 読込/保存は全履歴再演。記録外の変更は保存不可。save7直接readerの実測を現行leagueの正しさ保証として流用しない。
3. runtime指紋はまだpackage内の旧experimentを含む。新規実験は`experiments/`へ分離し、実験コードhashも記録する。旧snapshotは凍結sourceで読む。
4. 初期プロフィールは固定（p3/p9過小評価）。seed追加だけでは一般化しない。分布からの初期集団とラベル置換は次の研究候補。
5. 移籍窓は日28/56のみ。加齢、引退、新人、毎季市場、C、3D/UIは未実装。1000日安定は自律進化する世界全体の妥当性ではない。
6. 3つの世界の重複、直書き係数、clamp/round6重複は残る。全kernel化を先行せず、実験で必要な境界から交換する。

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
