# T010 事前実験計画（Protocol）— 2クラブ直接2D対戦と相互合意移籍を接続した最小キャリア閉ループ

登録日: 2026-11-07（実装・本実験実行前に凍結）
作成者: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**として本計画を策定・登録する。

## 1. 目的と検証仮説

T009の1クラブ出場機会閉ループを2クラブ（`club_a`: 監督 `c1`、初期所属 `p1, p2, p3`；`club_b`: 監督 `c2`、初期所属 `p4, p5, p6`）へ拡張し、週1回の直接2D対決（12試合）と中間移籍窓口（Day `28`, Day `56`）を備えた最小2クラブ世界（`world_kind='transfer_loop'`, `save=10`）において以下の4仮説を検証する。

- **H1（相互合意移籍による出場機会・技能成長の解放）**: 所属クラブ `club_a` で初期評価バイアスにより控えに置かれた高潜在選手 `p3` は、移籍窓口が有効な条件（`coupled_transfer`）において、戦力補強を求める `club_b`（監督 `c2`）からのオファーと3者合意を経て移籍し、移籍無効対照（`coupled_stay`）に比べて通算先発出場数・出場満足度・監督からの指導機会・最終技能成長量を有意に増加させる。
- **H2（育成と移籍が2クラブ直接2D対決の競技力収支に与える作用）**:
  1. 練習による3能力成長を試合へ反映する条件（`coupled_stay`, `coupled_transfer`）は、初期能力固定（`decoupled_stay`, `decoupled_transfer`）に比べて両クラブのパス成功数・シュート数・後半戦の競技水準を有意に変化させる。
  2. `coupled_transfer` と `coupled_stay` を比較すると、`p3` が `club_b`（`away`）へ移籍して先発定着することで、後半6試合（Day `35..84`）における `club_b`（`away`）のシュート数・ボール保持・得点（および `club_b - club_a` の後半得失点差）が有意に改善する。
- **H3（3当事者の独立認識による移籍成立の因果分離）**:
  - `fair_c1_prior_p3`（所属監督 `c1` の `p3` 初期評価のみを適正化）では、`p3` が `club_a` で先発起用されて満足度を保つため、移籍希望も放出同意も生じず移籍率が `0`（またはほぼ `0`）へ低下する。
  - `low_c2_scouting_p3`（相手監督 `c2` の `p3` 初期評価のみを低評価化）では、`p3` が不満から移籍希望を表明し `c1` も放出に同意するが、`c2` がオファーを出さないため移籍が成立しない。
  - `high_loyalty_p3`（`p3` のクラブ愛着のみを強化）では、`p3` がベンチに置かれ `c2` が獲得を望んでも、`p3` 自身が移籍希望を出さないため移籍が成立しない。
  - これら3対照により、移籍が固定イベントではなく **`p3`・`c1`・`c2` の3者の独立した主観認識と意思決定の合致** によってのみ生じることを実証する。
- **H4（情報境界・所属制約・1パス保存再開・独立監査）**: どのPolicy関数・移籍交渉関数も世界の真値（`MatchTraits`, `rates`, `cap`, 他者の私的記憶）を参照せず、各クラブの人数不変条件 `2 <= len(roster) <= 4` と所属監督からのみの助言受領が全日程で守られ、84日連続実行と42+42日保存再開、および1,000日＋14日再開が完全バイト一致する。

## 2. 環境・日程・初期プロフィール・数式仕様

### 2.1 2クラブ構成と日程
- **クラブと初期所属**:
  - `club_a`（`home` 側、監督 `c1`）: 初期ロスター `["p1", "p2", "p3"]`
  - `club_b`（`away` 側、監督 `c2`）: 初期ロスター `["p4", "p5", "p6"]`
  - ロスター人数制約: 各クラブとも常に `min_roster = 2`, `max_roster = 4`。
- **カレンダー（標準84日＝12週）**:
  - `day % 7 != 0`（72日間）: **育成日（Training Day）**。各監督は2枠/日（自クラブ選手への `observe` / `propose`、他クラブ選手への `scout`、または `rest`）。各選手は1枠/日（現所属クラブの監督からの提案のみを受領し、`A`, `B`, `rest` を選択）。
  - `day % 7 == 0`（Day `7, 14, ..., 84` の12日間）: **試合日（Match Day）**。両監督 `c1, c2` が自クラブ所属選手から先発2名を選考し、40 tickの連続2D試合（`club_a` = `home`, `club_b` = `away`）を実施。試合後、両監督は試合に出場した両クラブ先発計4名の統計を観測して評価を更新し、全6選手は自身の出場/ベンチ結果から満足度と連続ベンチ数を更新する。
  - `day in {28, 56}`（第4節・第8節の試合後）: **移籍窓口（Transfer Window）**。3者合意プロトコルを実行する。

### 2.2 6選手の初期プロフィール (`A` / `B`)

| 選手ID | 初期クラブ | 役割仮説 | 初期真値 `(pace, passing, shooting)` (平均 $\bar{s}$) | 上限 `cap` | 真の学習率 `(r_A, r_B)` | `autonomy` / `loyalty` | 2D `MatchStyle(direct, pass, press)` | `c1` 初期推定 `(skill, match)` | `c2` 初期推定 `(skill, match)` |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `p1` | `club_a` | `club_a` 主力技術型 | `(0.48, 0.51, 0.48)` (`0.490`) | `0.88` | `(0.110, 0.050)` | `0.28` / `0.12` | `(0.52, 0.62, 0.48)` | `(0.50, 0.54)` | `(0.49, 0.52)` |
| `p2` | `club_a` | `club_a` 主力機動型 | `(0.49, 0.45, 0.49)` (`0.477`) | `0.88` | `(0.050, 0.110)` | `0.30` / `0.12` | `(0.64, 0.44, 0.56)` | `(0.49, 0.53)` | `(0.48, 0.51)` |
| `p3` | `club_a` | 高潜在・`c1` 過小評価の挑戦者 | `(0.46, 0.47, 0.46)` (`0.463`) | `0.94` | `(0.150, 0.145)` | `0.26` / `0.00` | `(0.58, 0.56, 0.55)` | `(0.31, 0.29)` | `(0.48, 0.51)` |
| `p4` | `club_b` | `club_b` エース | `(0.49, 0.49, 0.50)` (`0.493`) | `0.89` | `(0.105, 0.105)` | `0.28` / `0.14` | `(0.58, 0.54, 0.54)` | `(0.49, 0.52)` | `(0.50, 0.54)` |
| `p5` | `club_b` | `club_b` 伸び悩む先発2番手 | `(0.41, 0.41, 0.41)` (`0.410`) | `0.78` | `(0.045, 0.045)` | `0.30` / `0.14` | `(0.54, 0.50, 0.50)` | `(0.41, 0.42)` | `(0.42, 0.43)` |
| `p6` | `club_b` | `club_b` 控え | `(0.39, 0.39, 0.39)` (`0.390`) | `0.76` | `(0.040, 0.040)` | `0.32` / `0.14` | `(0.52, 0.48, 0.48)` | `(0.38, 0.38)` | `(0.39, 0.39)` |

### 2.3 移籍窓口の3者相互合意スコア式 (`decide_transfer_window`)

移籍窓口日（Day `28`, Day `56`）において、選手ID順（`p1..p6`）に各選手 $i$（現所属クラブ $C_{\text{from}}$、監督 $c_{\text{from}}$、相手クラブ $C_{\text{to}}$、監督 $c_{\text{to}}$）について以下を評価する：

1. **選手の移籍希望得点 (`request_score`)**:
   $$U_{\text{req}}(i) = 0.55 \cdot \max(0.0, 0.48 - \text{satisfaction}_i) + 0.075 \cdot \min(4, \text{bench\_streak}_i) + 0.22 \cdot \max(0.0, \hat{s}_{i,\text{self}} - 0.42) - \text{loyalty}_i$$
   - $U_{\text{req}}(i) \ge \theta_{\text{req}} = 0.12$ のとき `wants_transfer = True`。
2. **放出側監督の放出同意 (`release_margin`)**:
   - 監督 $c_{\text{from}}$ が現所属メンバーの選考基礎値 $V_{c_{\text{from}}}(j) = 0.55 \hat{s}_{c_{\text{from}}, j} + 0.48 \hat{m}_{c_{\text{from}}, j}$ を比較し、上位2名（先発2枠）の2番手 $V_{c_{\text{from}}}(\text{rank}_2)$ に対して
   $$U_{\text{rel}}(c_{\text{from}}, i) = V_{c_{\text{from}}}(\text{rank}_2) - V_{c_{\text{from}}}(i) + 0.015 \cdot \min(4, \text{bench\_streak}_{c_{\text{from}}, i})$$
   - $|C_{\text{from}}| > 2$ かつ（選手 $i$ がクラブ内3位以下で $U_{\text{rel}}(c_{\text{from}}, i) \ge 0.0$）のとき `release_consented = True`。
3. **獲得側監督の獲得オファー (`offer_margin`)**:
   - 監督 $c_{\text{to}}$ の私的認識における選手 $i$ の評価 $V_{c_{\text{to}}}(i) = 0.55 \hat{s}_{c_{\text{to}}, i} + 0.48 \hat{m}_{c_{\text{to}}, i}$ と、自クラブ $C_{\text{to}}$ の現メンバー2番手 $V_{c_{\text{to}}}(\text{rank}_2)$ の差：
   $$U_{\text{offer}}(c_{\text{to}}, i) = V_{c_{\text{to}}}(i) - V_{c_{\text{to}}}(\text{rank}_2)$$
   - $|C_{\text{to}}| < 4$ かつ $U_{\text{offer}}(c_{\text{to}}, i) \ge \theta_{\text{offer}} = 0.025$ のとき `offer_made = True`。
4. **選手のオファー受諾 (`accept_score`)**:
   - 選手 $i$ は公開されている直近試合の先発出場履歴から相手クラブ先発の推定壁 $\bar{m}_{\text{opp}}$ を参照し、自身の自己評価との差および相手監督への信頼 $\text{trust}_{i, c_{\text{to}}}$ から受諾得点を計算する：
   $$U_{\text{acc}}(i, c_{\text{to}}) = 0.50 \cdot (\hat{s}_{i,\text{self}} - \bar{m}_{\text{opp}}) + 0.30 \cdot (\text{trust}_{i, c_{\text{to}}} - 0.50) + 0.25 \cdot \max(0.0, 0.48 - \text{satisfaction}_i) - \text{loyalty}_i$$
   - $U_{\text{acc}}(i, c_{\text{to}}) \ge \theta_{\text{acc}} = 0.05$ のとき `offer_accepted = True`。
5. **成立条件**:
   - `wants_transfer and release_consented and offer_made and offer_accepted` の4条件がすべて `True` のときのみ、所属クラブを $C_{\text{from}} \to C_{\text{to}}$ へ変更し、`bench_streak = 0`、`playing_time_satisfaction = max(satisfaction, 0.50)` に更新して `transfer_window_decision` イベントへ全4スコアと根拠記憶IDを記録する。

## 3. 実験設計・Seed分割・評価指標

- **Seed分割**: 探索セット `1..30`（30 seeds）、確認セット `100..129`（30 seeds、対応seedペアードBootstrap 2,000回 95% CI）。
- **主比較4条件（計240 runs = 4条件 × 60 seeds × 84日）**:
  1. `decoupled_stay`: `coupling='decoupled'`, `transfer='disabled'`
  2. `coupled_stay`: `coupling='coupled'`, `transfer='disabled'`
  3. `decoupled_transfer`: `coupling='decoupled'`, `transfer='enabled'`
  4. `coupled_transfer`: `coupling='coupled'`, `transfer='enabled'`
- **単一要因の追加対照3条件（確認30 seeds `100..129`、計90 runs）**:
  5. `fair_c1_prior_p3`: `coupled_transfer` ＋ `c1` の `p3` 初期評価のみ `(0.31, 0.29) -> (0.50, 0.54)`
  6. `low_c2_scouting_p3`: `coupled_transfer` ＋ `c2` の `p3` 初期評価のみ `(0.48, 0.51) -> (0.31, 0.29)`
  7. `high_loyalty_p3`: `coupled_transfer` ＋ `p3` の `loyalty` のみ `0.00 -> 0.35`
- **主要評価指標**:
  - 移籍とキャリア指標: `total_transfers`, `p3_transferred`（0/1率）、`p3_final_club`、`p3_appearances`（全体および `club_a` / `club_b` 別）、`p3_skill_gain`、6選手平均 `mean_skill_gain`、`p3_playing_time_satisfaction`、`p3_proposals_from_c1`, `p3_proposals_from_c2`。
  - 2クラブ直接対決の競技指標（通算12試合および後半6試合 Day `35..84`）: `club_a_goals`, `club_b_goals`, `club_b_minus_club_a_gd`, `second_half_club_b_minus_club_a_gd`, `club_b_shots`, `club_b_pass_completions`, `club_b_possession_ticks`。
  - 行動不一致率: 対応seed間の移籍有無不一致率、両監督の先発選考不一致率、両監督の育成行動不一致率、6選手の練習行動不一致率。
