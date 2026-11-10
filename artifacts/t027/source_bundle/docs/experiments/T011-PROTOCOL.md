# T011 事前実験プロトコル: 4クラブ総当たりリーグ・有限資金台帳・育成還元配当の閉ループ (`world_kind="league_economy_loop"`, `save=11`)

- **登録日**: 2026-11-07（実装前登録）
- **作成者 (Author)**: **Antigravity (Gemini 4 Argon)** — ※T001〜T007の既存作成者（`Sol 6.1 High`）とは異なる作成者による計画・実装・検証
- **対象タスク**: [T011.md](../tasks/T011.md)
- **関連判断**: [DECISIONS.md](../DECISIONS.md) (`D025`)

---

## 1. 研究仮説 (Pre-registered Hypotheses)

- **H1（複数クラブ市場における人材移動とキャリア開花: `open_solidarity` vs `closed_solidarity`）**:
  4クラブ（育成型 `club_a, club_c`、補強型 `club_b, club_d`）環境において移籍窓口を開放すると、初期所属クラブで過小評価されていた高潜在若手選手（`p3` in `club_a`, `p9` in `club_c`）が、補強資金と先発枠ニーズを持つクラブ（`club_b, club_d`）からの競合オファーを3者合意で受諾して移籍し、`p3, p9` の先発出場数・出場満足度・技能成長量が `closed_solidarity` 比で有意に増加する。
- **H2（育成還元配当による育成クラブの設備再投資と成長・競争力の維持: `open_solidarity` vs `open_no_solidarity`）**:
  移籍市場が開放されているとき、育成還元配当がない条件（`open_no_solidarity`）では若手を放出した育成クラブ（`club_a, club_c`）の資金が安全準備金を割り込み、育成設備稼働日数（`academy_facility_days`）と育成クラブ残留選手の技能成長（`academy_clubs_skill_gain`）、および育成クラブの獲得勝点（`academy_clubs_points`）が低下する。これに対し、適正移籍金＋育成連帯金＋移籍後出場配当が還流する `open_solidarity` では、育成クラブへ資金が還流して設備稼働日数・育成クラブ成長量・育成クラブ勝点が有意に改善し、補強クラブと育成クラブの間の資金集中度（クラブ現金格差）が緩和される。
- **H3（厳密な資金保存則と攻撃的Policy・不正操作への耐性）**:
  1. 全ラン・全ステップにおいて `initial_total_cash + cumulative_external_inflow == sum(club_cash) + sum(player_wages) + cumulative_external_outflow` が誤差 `0` で成立し、破綻（負の現金残高）は `0` 件である。
  2. 外部流入ゼロ対照（`fixed_budget_no_inflow`）では初期資金 `160.0` の枠内で自動的に設備投資と移籍支出が抑制され、保存則が厳密に保たれる。
  3. 買占め対照（`hoarder_buyer_policy`）ではロスター上限 `MAX_CLUB_ROSTER = 4` と予算制約により単一クラブへの無限集中が阻止される。
  4. 敵対的循環移籍・二重請求対照（`exploit_churn_guard`）では、同一シーズン内の循環移籍（`A -> B -> A`）および同一 `(transfer_id, match_id)` の育成配当二重請求が100%拒否される（`actual_duplicate_claims == 0`）。

---

## 2. 実験条件とシード設計

- **シード集合**:
  - 探索用シード (`exploration`): `1..30`（30 seeds）
  - 確認用シード (`confirmation`): `100..129`（30 seeds）
  - 対応のあるブートストラップリサンプリング: `2,000` 回（`BOOTSTRAP_SEED = 10011`）
- **主実験 ($2 \times 2$ 要因計画, 4条件 × 60 seeds × 84日 = 240 runs)**:
  1. `closed_no_solidarity`: `market_mode="closed"`, `solidarity_mode="disabled"`
  2. `closed_solidarity`: `market_mode="closed"`, `solidarity_mode="enabled"`
  3. `open_no_solidarity`: `market_mode="open"`, `solidarity_mode="disabled"`
  4. `open_solidarity`: `market_mode="open"`, `solidarity_mode="enabled"`
- **単一要因・攻撃的対照実験 (Confirmation Seeds `100..129`, 3条件 × 30 seeds = 90 runs)**:
  5. `fixed_budget_no_inflow`: `open_solidarity` に対し、試合日の外部興行収入流入を `0.0` に固定。
  6. `hoarder_buyer_policy`: `open_solidarity` に対し、`club_b, club_d` の獲得オファー閾値を `-0.08` 引き下げて積極買占めを試行。
  7. `exploit_churn_guard`: `open_solidarity` に対し、第2窓口（Day 56）での元クラブへの即時出戻り移籍試行と、試合後の同一 `(transfer_id, match_id)` 配当二重請求試行を注入し、ガードによる拒否を検証。

---

## 3. 主要評価指標

- **市場・キャリア指標**:
  - `total_transfers`: 通算成立移籍件数
  - `talents_transferred_rate`: 高潜在若手2名（`p3`, `p9`）の移籍成立率（`0.0`, `0.5`, `1.0`）
  - `talents_appearances`: `p3, p9` の平均出場試合数（最大12試合）
  - `talents_satisfaction`: `p3, p9` の最終出場満足度平均
  - `talents_skill_gain`: `p3, p9` の平均総合スキル成長量
- **育成還元・設備投資・クラブ間バランス指標**:
  - `solidarity_dividends_paid`: 育成クラブ（`club_a, club_c`）へ支払われた移籍連帯金＋出場活躍配当の総額
  - `academy_facility_days`: 育成クラブ（`club_a, club_c`）の合計設備稼働日数（最大 `72 × 2 = 144` club-days）
  - `academy_clubs_skill_gain`: 育成クラブ初期所属選手（または残留選手）の平均スキル成長量
  - `academy_clubs_points`: 育成クラブ（`club_a, club_c`）の合計勝点
  - `buyer_minus_academy_points`: 補強クラブ（`club_b, club_d`）合計勝点 − 育成クラブ（`club_a, club_c`）合計勝点
  - `buyer_minus_academy_gd`: 補強クラブ合計得失点差 − 育成クラブ合計得失点差
  - `club_cash_gini_gap`: 補強クラブ最終現金 − 育成クラブ最終現金
- **台帳健全性・防御指標**:
  - `ledger_discrepancy`: 台帳保存則の誤差（常に `0.0` であること）
  - `insolvency_events`: 現金不足による債務不履行件数（常に `0` であること）
  - `blocked_circular_transfers`: 阻止された循環移籍試行件数
  - `blocked_duplicate_claims`: 阻止された二重配当請求試行件数
  - `actual_duplicate_claims`: 実際に通過してしまった二重配当件数（常に `0` であること）
