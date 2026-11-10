# T009 事前実験計画（Protocol）— 育成・監督選考・2D競技を接続した最小の出場機会閉ループ

登録日: 2026-11-07（実装・本実験実行前に凍結）
作成者: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**として本計画を策定・登録する。

## 1. 目的と検証仮説

T001〜T007の育成・認識核と、T008の最小連続2D競技核（`match_2d`）を接続し、**1クラブ（監督1人 `c1`、選手3人 `p1, p2, p3`、先発2枠＋控え1枠）×84日（週1試合×12試合＋練習72日）** の最小キャリア閉ループ（`world_kind='selection_loop'`, `save=9`）において、以下の4仮説を検証する。

- **H1（育成 $\to$ 2D競技の順方向作用）**: 日々の練習による3能力 `MatchTraits(pace, passing, shooting)` の成長を2D試合へ反映する条件（`coupled_prior`）は、初期能力固定で試合を行う対照（`decoupled_prior`）に比べて、後半および通算のパス成功数・シュート数・ボール保持tick・得失点差を有意に増加させる。
- **H2（2D競技 $\to$ 監督選考・選手行動の逆方向作用）**: 2D試合の出場・遂行結果を監督の評価と選手の出場満足度へ還流する条件（`decoupled_feedback` および `coupled_feedback`）は、事前・練習評価のみで選考する条件（`decoupled_prior`, `coupled_prior`）に比べて、先発選考の組み合わせ（`p1, p2, p3` の出場機会配分）、監督の観察/提案対象、選手の練習選択に測定可能な不一致を生む。
- **H3（初期評価バイアスと出場機会によるキャリア分岐）**: 真の初期能力はやや低いが潜在学習率と上限が高い挑戦者 `p3` に対し、監督の初期評価が低い基準条件（`coupled_feedback`, $\hat{s}_{c1,\text{p3}}(0)=0.39$）と、真の能力変更なしに監督の初期評価だけを適正化した対照（`fair_prior_p3`, $\hat{s}_{c1,\text{p3}}(0)=0.44$）、およびベンチ連続へのローテーション配慮を入れる対照（`rotation_feedback`）を比較すると、同じ真の潜在能力からでも出場試合数・監督からの指導機会・最終技能に有意な分岐が生じる。
- **H4（情報境界・保存再開・1パス検証）**: 監督の練習行動・先発選考、選手の練習選択・2D試合行動のいずれも世界の真値（真の `MatchTraits`, `rates`, `cap`, 相手内部状態）を参照せず、84日連続実行と42+42日保存再開がバイト一致し、1000日＋14日再開でも整合性が保たれる。

## 2. 環境・日程・状態・数式仕様

### 2.1 構成個体と日程
- **カレンダー**: 標準84日（12週）。`day % 7 != 0` の72日間は**育成日（Training Day）**、`day % 7 == 0`（Day `7, 14, ..., 84`）の12日間は**試合日（Match Day）**。
- **育成日予算**: 監督 `c1` は2枠/日（対象選手の前後観察付きセッション `observe`、方法 `A`/`B` の提案 `propose`、または休息 `rest`）。選手 `p1, p2, p3` は各1枠/日（方法 `A`、方法 `B`、または `rest`）。
- **試合日進行**:
  1. `morning_selection`: 監督 `c1` が私的認識のみから先発2名（`slot 0`, `slot 1`）と控え1名（`bench`）を決定する。
  2. `match_episode`: 先発2名が `home`（`h1`, `h2`）として、固定基準の対戦相手 `away`（`a1`, `a2`: `pace=0.52, passing=0.52, shooting=0.52`）と40 tickの連続2D試合（`soccer_sim/match_2d.py` の物理・局所観測・意思決定ルール）を行う。
  3. `post_match_update`:
     - **A（身体・技能状態）**: 先発2名は試合負荷により疲労が $+\Delta f_{\text{match}} = 0.06 + 0.002 \cdot (\text{tackles} + \text{shots})$ 増加する一方、実戦経験による技能成長 $\Delta s_{\text{match}} = 0.036 \cdot (1 - \bar{s}_i / \text{cap}_i) \cdot (0.55 + 0.08 \cdot \text{goals}_i + 0.03 \cdot \text{pass\_comp}_i + 0.025 \cdot \text{shots}_i + 0.02 \cdot \text{tackles\_won}_i)$ を重み `(0.95, 1.05, 1.00)` で `(pace, passing, shooting)` に獲得する。控え1名は実戦成長が `0` である代わりに休養により疲労が $-0.05$ 回復する。
     - **B（監督認識・選手キャリア記憶）**: 監督は**先発2名のみ**の試合統計から試合評価 $\hat{m}_{c1, i}$ と技能評価 $\hat{s}_{c1, i}$（`match_feedback='feedback'` のとき）および疲労推定を更新する（控え選手は試合に出場していないため試合評価・最終試合観察日は更新されない）。全3選手は自身の出場/ベンチ結果から出場満足度 `playing_time_satisfaction` と自己試合評価を更新する。

### 2.2 選手初期プロフィールと3能力成長写像 (`A`)

| 選手ID | 役割仮説 | 初期真値 `(pace, passing, shooting)` (平均 $\bar{s}$) | 上限 `cap` | 真の学習率 `(r_A, r_B)` | 初期期待 `(G_A, G_B)` / 好み `(pref_A, pref_B)` | `autonomy` / `trust` | 2D `MatchStyle(direct, pass, press)` | 監督初期推定 `(skill, match_rating)` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `p1` | 技術型先発候補 | `(0.45, 0.48, 0.45)` (`0.460`) | `0.88` | `(0.115, 0.045)` | `(0.046, 0.042)` / `(0.014, 0.010)` | `0.28` / `0.70` | `(0.52, 0.62, 0.48)` | `(0.47, 0.52)` |
| `p2` | 機動型先発候補 | `(0.46, 0.42, 0.47)` (`0.450`) | `0.88` | `(0.050, 0.115)` | `(0.042, 0.046)` / `(0.010, 0.015)` | `0.32` / `0.66` | `(0.64, 0.44, 0.56)` | `(0.46, 0.51)` |
| `p3` | 高潜在・初期過小評価の挑戦者 | `(0.43, 0.43, 0.43)` (`0.430`) | `0.93` | `(0.145, 0.135)` | `(0.018, 0.018)` / `(0.006, 0.006)` | `0.26` / `0.72` | `(0.58, 0.56, 0.55)` | `(0.32, 0.30)` (`fair_prior_p3` では `(0.47, 0.53)`) |

- **練習による3能力更新**:
  - 既存の `resolve_training` と同じ減衰付き成長式で基本成長量 $\Delta s = r_{i, m} \cdot (1 - \bar{s}_i / \text{cap}_i) \cdot (1 - 0.55 f_i) \cdot 0.25$ を算出し、方法別重みベクトル $\mathbf{w}_A = (0.45, 1.45, 1.10)$, $\mathbf{w}_B = (1.45, 0.45, 1.10)$ を掛けて各能力 $(\text{pace}, \text{passing}, \text{shooting})$ を上限 `cap` の範囲内で更新する。
- **試合時の実効能力**:
  - `training_match_coupling='coupled'` のとき、試合開始時の疲労 $f_i$ に応じて実効走力 $\text{pace}_{\text{eff}} = \text{clamp}(\text{pace}_i \cdot (1 - 0.18 f_i), 0.15, 1.0)$、実効技術 $(\text{passing}_{\text{eff}}, \text{shooting}_{\text{eff}}) = \text{clamp}((\text{passing}_i, \text{shooting}_i) \cdot (1 - 0.10 f_i), 0.15, 1.0)$ を2D試合エンジンへ渡す。
  - `training_match_coupling='decoupled'` のとき、初期真値 $\theta_i(0)$ をそのまま2D試合エンジンへ渡す。

### 2.3 監督の先発選考式と試合後認識更新 (`B` / `Policy`)

- **先発選考得点 (`decide_lineup`)**:
  $$U_{\text{select}}(i) = 0.55 \cdot \hat{s}_{c1, i} + 0.48 \cdot \hat{m}_{c1, i} - 0.22 \cdot \hat{f}_{c1, i} + w_{\text{opp}} \cdot \min(6, \text{bench\_streak}_{c1, i})$$
  - `match_feedback='prior'` では $\hat{m}_{c1, i}$ が初期事前評価のまま固定され、`'feedback'` では毎試合の観察から $\hat{m}_{c1, i}$ と $\hat{s}_{c1, i}$ が更新される。
  - `selection_policy='merit'`（既定）では $w_{\text{opp}} = 0.0$、`'rotation'` では $w_{\text{opp}} = 0.065$。
  - 上位2名を `slot 0`, `slot 1`（先発）、3位を `bench`（控え）とする。
- **試合後の監督評価更新 (`match_feedback='feedback'` のみ)**:
  - 先発選手 $i$ の観測試合パフォーマンス指標:
    $$r_{\text{obs}, i} = \text{clamp}\bigl(0.28 + 0.18 \cdot \text{goals}_i + 0.035 \cdot \text{shots}_i + 0.035 \cdot \text{pass\_comp}_i + 0.018 \cdot \text{tackles\_won}_i + 0.007 \cdot \text{poss\_ticks}_i - 0.095 \cdot \text{opp\_goals} + \epsilon_{\text{match}, i},\ 0.0,\ 1.0\bigr)$$
  - 監督の試合評価更新: $\hat{m}_{c1, i} \leftarrow (1 - 0.35)\hat{m}_{c1, i} + 0.35 r_{\text{obs}, i}$。
  - 監督の技能推定への試合ブレンド: $\hat{s}_{c1, i} \leftarrow \text{clamp}((1 - 0.18)\hat{s}_{c1, i} + 0.18 r_{\text{obs}, i}, 0.0, 1.0)$、最終観察日 `last_seen` を試合日で更新。控え選手は試合に出場していないため更新されない。
- **選手のキャリア認識と練習選択への作用**:
  - 先発出場時は出場満足度 `satisfaction` が $\text{clamp}(s + 0.08, 0, 1)$ へ上昇し `bench_streak = 0`。ベンチ時は $\text{clamp}(s - 0.09, 0, 1)$ へ低下し `bench_streak += 1`。
  - `match_feedback='feedback'` では、選手の練習候補（`A`, `B`）の採点に**出場渇望ドライブ** $\Delta_{\text{drive}} = 0.022 \cdot \max(0.0, 0.50 - \text{satisfaction})$ が加わり、さらに実効自律性が $\text{autonomy}_{\text{eff}} = \text{clamp}(\text{autonomy} + 0.18 \cdot \max(0.0, 0.45 - \text{satisfaction}), 0, 1)$ へ高まる（ベンチが続く選手ほど自主練習を優先しやすくなる）。

## 3. 実験設計・Seed分割・評価指標

- **Seed分割**:
  - 探索セット（Exploration）: `1..30`（30 seeds）
  - 確認セット（Confirmation）: `100..129`（30 seeds）— 主報告・仮説判定は確認セット30 seedsの対応差（seed単位ペアードbootstrap 2,000回、95% CI）で行う。
- **主比較4条件（計240 runs = 4条件 × 60 seeds × 84日）**:
  1. `decoupled_prior`: `coupling='decoupled'`, `feedback='prior'`
  2. `coupled_prior`: `coupling='coupled'`, `feedback='prior'`
  3. `decoupled_feedback`: `coupling='decoupled'`, `feedback='feedback'`
  4. `coupled_feedback`: `coupling='coupled'`, `feedback='feedback'`
- **追加対照3条件（確認30 seeds `100..129`、計90 runs）**:
  5. `rotation_feedback`: `coupled_feedback` ＋ `selection_policy='rotation'`（$w_{\text{opp}} = 0.065$）
  6. `fair_prior_p3`: `coupled_feedback` ＋ 監督の `p3` 初期技能評価を `0.32 -> 0.47`（初期試合評価 `0.30 -> 0.53`）へ変更（真の能力・学習率は一切不変）
  7. `p1_pass_style_plus`: `coupled_feedback` ＋ `p1` の2D `MatchStyle.pass_bias` を `0.62 -> 0.84`, `directness` を `0.52 -> 0.36` へ変更
- **主要評価指標**:
  - 競技成果（12試合計・後半6試合計）: `home_goals`, `away_goals`, `goal_diff`, `home_possession_ticks`, `home_pass_completions`, `home_shots`, `home_tackles_won`。
  - 出場機会と選考多様性: 各選手（`p1, p2, p3`）の先発出場試合数（`appearances` / 12試合）、最大連続ベンチ数（`max_bench_streak`）、先発ペア切替回数（`lineup_switches`）、出場機会ジニ係数または最小出場選手枠。
  - 育成・認識・行動: 各選手の最終平均技能 gain（`p1_gain, p2_gain, p3_gain`, 3人平均 `mean_skill_gain`）、能力別終値（`pace, passing, shooting`）、出場満足度終値、監督の選手別提案数・観察数、対応seed間の先発選考不一致率・監督行動不一致率・選手練習行動不一致率。
