# T008 レビュー — 独立判断が作用する最小の2D競技核

レビュー日: 2026-11-07
**作成者・レビュアー**: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**として、T008の事前計画・実装・実験・独立監査を実施した。

## 完了条件の照合

| 完了条件（[docs/tasks/T008.md](../tasks/T008.md)） | 検証結果 | 根拠・成果物 |
|---|---|---|
| 不正な二重所持、範囲外の値、過大移動、無費用の複数行動、無原因の得点がない。非保有者のキックなど不可能な意図の扱いを検査する | **合格** | `validate_match_world`・`test_physical_invariants_and_accounting`・`test_illegal_non_owner_kick_logged_and_demoted_to_hold`・[audit_match.py](../../artifacts/t008/review/audit_match.py) で確認。非保有者の `pass`/`shoot` は `intent_status='illegal_without_ball'` として記録され `hold`（移動0）へ降格する |
| Policyへ真値が漏れず、隠れた相手能力を変えても観測が同じ時点の意思決定は不変。実行結果は能力変更で変わってよい | **合格** | `decide_match_player` は `LocalMatchObservation` と本人の `PlayerMatchMind` のみを受け取る。`test_non_leakage_of_opponent_traits_mind_and_unseen_positions` で相手能力・相手スタイル・視野外座標を変更しても観測と意思決定が不変であり、物理移動距離のみ能力差で変わることを確認 |
| 対称fixtureの左右反転、ID/処理順交換で対応する結果が一致。同点の規則が非対称なら理由と感度を明示する | **合格** | `test_mirror_symmetry_and_order_invariance_and_mutual_rebound` および `symmetry_and_order` probe（各10 seed）で確認。異チーム間のルーズボール完全同点はID順を使わず中点反発（`mutual_rebound`）とし、同チーム内の同点のみ `slot` 順（`1 < 2`）で解決する規則を明記 |
| 同じ初期状態で位置・実行能力・Policyのうち1要因だけを変える介入を用意する。成功パス、奪取、シュート、所持推移、行動分布を測り、得点だけで合否を決めない | **合格** | `baseline`, `home_skill_plus`, `home_position_plus`, `home_pass_style` の4条件×60 seed＝240 runを [comparison.json](../../artifacts/t008/experiments/comparison.json) に保存。能力介入は所持・シュート・得失点差、位置介入は敵陣滞在と初動所持、スタイル介入はパス試行/成功数（`+10.53` / `+8.67`）と行動分布をそれぞれ有意に変化させた |
| 全員シュート反復、ボール近傍待機などの単純対照で一動作が無条件に支配しないか調べる | **合格** | `home_always_shoot`（得失点差 `-2.80` vs `baseline`）と `home_ball_camp_wait`（得失点差 `-2.90` vs `baseline`）を確認30 seedおよび `test_degenerate_strategies_do_not_dominate_baseline` で検証 |
| 同seed、連続/保存再開で状態・ログ一致。短いエピソードから位置とボールの動きを後で再生できる。実行時間/ログ量を測る | **合格** | 代表run（40 tick vs 20+20 tick再開）と単体テスト全4条件でバイト一致を確認。各tickの `tick_summary` から全選手・ボールの座標推移を完全再生可能。1000 tick実行 `0.287s`・読込 `0.152s`・約5.38 MBを実測 |
| T001〜T007の凍結成果物を保持。実験結果と限界を記録し、育成との接続を次に進められるか判断する | **合格** | T001〜T007の凍結340ファイルのSHA-256一致、T007全240 runの意味投影一致、[T007凍結ソースバンドル](../../artifacts/t007/source_bundle/manifest.json) からの旧snapshot再開を [verification.json](../../artifacts/t008/review/verification.json) で確認 |

## 限界と次段階（M4）への判断

1. **競技ルールの最小性**:
   - T008は2対2・40 tickの最小連続2D核であり、オフサイド、ファウル、スローイン/コーナーキックのセットプレー再開（現在はタッチライン/ゴールライン外でクランプ停止）、GK専用ルール、試合中の疲労消耗を含まない。
2. **完全対称・ノイズ0時の同着反発膠着**:
   - 異チーム間のルーズボール同着をID順で独占させない `mutual_rebound` 規則を採用したため、`noise_scale = 0.0` かつ初期位置・能力が完全鏡像の条件（`baseline`, `home_pass_style`）では、中央で `mutual_rebound` が繰り返される決定論的膠着が生じる。通常の確率環境（`noise_scale = 1.0`）または僅かな非対称配置では発生しないが、将来キックオフを片側チームのパス開始にする場合は先攻チーム有利とのトレードオフを比較する必要がある。
3. **育成（T001〜T007）との未接続**:
   - T008では競技に必要な最小能力を `MatchTraits(pace, passing, shooting)` の3軸と `MatchStyle(directness, pass_bias, press_bias)` に分離した。T001〜T007の育成ループは単一 `skill` と方法 `A`/`B` で構成されているため、次段階（M4: 出場機会・競争・成長の循環）へ進む際は、「方法A/Bが `pace`/`passing`/`shooting` のどれを伸ばすか」または「複数監督の起用判断がどの指標（得点だけでなくパス成功・奪取・練習観察）を参照するか」を実装前に事前登録する必要がある。
4. **判断**:
   - 独立判断が空間移動・パス・奪取・シュート・得点へ作用する最小2D競技核の境界と再現性が確認できたため、T008を完了とし、次はM4（育成と競技・出場機会の接続）のタスク定義へ進める状態にあると判断する。
