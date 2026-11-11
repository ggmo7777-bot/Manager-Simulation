# T009 レビュー（敵対的監査・完了条件照合・限界と次段階への示唆）

記録日: 2026-11-07
監査・作成者: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**としてT009を監査・記録する。

## 1. 完了条件（Acceptance Criteria）照合

[docs/tasks/T009.md](../tasks/T009.md) および [docs/GOVERNANCE.md](../GOVERNANCE.md) の完了条件に対する照合結果は以下の通り。

| 項目 | 要件 | 検証結果 | 根拠ファイル |
| :--- | :--- | :---: | :--- |
| 1. 作成者明記 | T001〜T007の作成者（`Sol 6.1 High`）とは別主体であることをコード・成果物・文書に明記 | **合格** | `SELECTION_AUTHOR = "Antigravity (Gemini 4 Argon)"`, `summary.json`, `verification.json` |
| 2. 情報境界（A/B/Policy分離） | 監督・選手のどのPolicy関数も世界の真値（`MatchTraits`, `rates`, `cap`, 相手内部状態）を参照しない | **合格** | `tests/test_t009.py::test_non_leakage_in_coach_and_player_and_lineup_policies` |
| 3. 1パス保存・再開 | 再演なしに1パスで完全復元し、84日連続実行と42+42日再開、1,000+14日再開が完全バイト一致 | **合格** | `tests/test_t009.py::test_checkpoint_save_resume_exact_equality_all_conditions`, `summary.json` |
| 4. 破損検知（Fail-fast） | 12種類の改ざん・不整合snapshotを確実に拒否 | **合格** | `tests/test_t009.py::test_corrupted_snapshots_rejected` |
| 5. 双方向作用の実証 | 育成$\to$2D試合（H1）、2D試合$\to$選考・行動（H2）、初期認識/ローテーション/2Dスタイル介入（H3）を確認30 seedsで確認 | **合格** | [docs/experiments/T009-RESULTS.md](T009-RESULTS.md), `artifacts/t009/experiments/summary.json` |
| 6. 独立1パス意思決定監査 | Policy関数を一切呼ばず（`policy_functions_called == 0`）1,860決定＋372フィードバックを独立再計算 | **合格** | [artifacts/t009/review/selection-audit.json](../../artifacts/t009/review/selection-audit.json) |
| 7. 過去資産保全 | T001〜T008の凍結421ファイル無改変、T008の240 runs意味射影一致、全101単体テスト合格 | **合格** | [artifacts/t009/review/verification.json](../../artifacts/t009/review/verification.json), [artifacts/t009/review/tests.json](../../artifacts/t009/review/tests.json) |

## 2. 開発中に発見・修正した設計上の教訓（自己監査記録）

1. **2D試合イベント辞書キーの整合性**:
   - 初期実装では2D試合ログから選手別統計を抽出する際、汎用イベント名（`actor`, `ball_acquire`）を想定していたが、`soccer_sim/match_2d.py` の実イベントは `ball_kick`（`kicker_id`）、`ball_control`（`from_player_id`）、`tackle_resolution`（`challenger_id`）を使用していたため、当初はパス成功数・シュート数が選手別統計へ反映されていなかった。
   - `_extract_starter_match_stats` を `match_2d.py` の実スキーマへ厳密に合わせたことで、`coupled_feedback` のパス成功数・シュート数・タックル成功数が正確に監督の試合後認識へ伝播するようになった。
2. **試合疲労と実戦経験成長のバランス**:
   - 当初、先発出場時の疲労増分を `+0.12`、控え休養を `-0.10`（1試合あたり `0.22` の疲労差）としていたところ、先発出場した選手ほど翌週の練習日に `rest` を多く選ぶため、出場機会が増えた `p3` の通算技能成長量がベンチ固定時よりわずかに低くなる逆転が生じた。
   - 先発疲労増分を `+0.06 + 0.002*(tackles_won + shots)`、控え回復を `-0.05`、実戦経験係数を `0.036` に調整した結果、「試合出場は翌週の休息選択を適度に増やす（`p3_rest_days`: `28.57 -> 30.70` 日）が、実戦経験と出場渇望ドライブを通じて通算成長量を有意に高める（`p3_skill_gain`: `0.3849 -> 0.3997`, `fair_prior_p3` では `0.4046`）」という自然なキャリア動態が成立した。

## 3. 現段階の限界と次段階（T010以降）への課題

1. **控え選手の途中交代・ポジション役割分担が未導入**:
   - T009では試合単位の先発2枠・控え1枠の選考としたため、試合中の疲労やスコア状況に応じた途中交代（substitution）や、先発2枠内の役割差（守備的アンカー vs 前線ストライカーなど）はまだ扱っていない。
2. **対戦相手が固定基準チーム（`away`）**:
   - 現在は1クラブ内の出場機会閉ループを純粋に分離するため、対戦相手 `away` を固定パラメータ `(0.52, 0.52, 0.52)` とした。次段階（M4後半〜M5）では、T007の複数監督・複数クラブ構造と統合し、異なる育成方針・選考方針を持つ2クラブ以上がリーグ形式で対戦・移籍・適応し合う歴史生成へ拡張できる。
3. **選手個別の3能力認識の細分化**:
   - 現在の監督・選手の認識状態は総合技能推定 `skill_estimate` と試合評価 `match_rating_estimate`、方法別残差 `method_residuals` で構成されている。今後、監督が「走力は高いがパスが課題」といった能力別（`pace`, `passing`, `shooting`）の多次元認識を持つようにすれば、練習方法 `A`（技術重視）と `B`（走力重視）の提案がさらに戦術的になる。
