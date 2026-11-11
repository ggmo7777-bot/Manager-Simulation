# EXP-T008 独立判断が作用する最小の2D競技核

登録: 2026-11-07、実装/結果観察前。
**作成者**: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**として、本事前計画の策定・実装・検証を担当する。

目的は、4選手・1ボール・2ゴール・固定tickの最小2D競技核において、**位置・実行能力・局所観測・個人Policyを分離し、各選手の毎tick独立判断が空間移動・パス・奪取・シュート・得点という再現可能な競技履歴を生むこと**を検証することである。得点や勝率の最大化は目的とせず、戦術の一般妥当性や長期育成との結合はこの検査だけで確定しない。

## 範囲と実装境界

- **世界種別**: `world_kind = 'match_2d'`, `save = 8`, `rules = 't008-match2d-v1'`, `policy = 't008-match2d-score-v1'`。
- **既存資産との分離**: T001〜T007の単一監督・複数監督の育成世界（`single_coach`, `multi_coach`）は一切書き換えず、`soccer_sim/match_2d.py` と `soccer_sim/experiment_t008.py` を追加する。T007までの凍結成果物（340ファイル）および `artifacts/t007/source_bundle` を完全保持する。
- **空間と時間**:
  - 連続座標2Dピッチ: $x \in [-10.0, 10.0]$, $y \in [-6.0, 6.0]$。
  - ゴール幅: $y \in [-1.8, 1.8]$。`home`（`h1`, `h2`）は $+x$ 側ゴール（$x = +10.0$）へ攻め、`away`（`a1`, `a2`）は $-x$ 側ゴール（$x = -10.0$）へ攻める。
  - 標準エピソード長: `40` tick（1 tick = 1同時意思決定＋物理・競合解決ステップ）。
- **状態の3層分離**:
  - **World真値 (`PlayerMatchState`, `BallState`)**: 選手の真の座標 $(x, y)$ と `MatchTraits(pace, passing, shooting)`、ボールの座標・速度・単一所有者 `owner_id`・最終タッチ選手 `last_touch_id`。
  - **個人の心 (`PlayerMatchMind`)**: 選手固有の判断傾向 `MatchStyle(directness, pass_bias, press_bias)` と、外部から与えられた固定方針ラベル `coach_directive`（`'balanced'` または `'press'`）。相手能力や相手私的状態のキャッシュは持たない。
  - **局所観測 (`LocalMatchObservation`)**: 各tick開始時に本人視点（攻撃方向が常に $+x_{\text{rel}}$ になる正規化座標系）で生成される観測。視野半径 `vision_radius = 6.5` 内の他選手は位置観測ノイズ付きで可視（`visible = True`）、視野外は不可視（`visible = False, position = None`）となる。**相手の `MatchTraits`・`PlayerMatchMind`・当tick意図は一切含めない**。研究用対照として `oracle` モード（視野無限・ノイズ0、ただし能力真値は非公開）を別条件で用意する。

## 行動集合と物理・競合解決ルール

各選手は毎tick、自分の `LocalMatchObservation` と `PlayerMatchMind` のみから以下の5行動候補を採点し、最高得点の1行動を意図（`MatchIntent`）として提出する。全選手の意図を先に収集し、処理順やID順に依存しない同時解決を行う。

1. **行動候補（5種）**:
   - `move`: 8方向の単位ベクトルまたはボール/ゴール方向への移動意図。
   - `pass`: 味方への地上パス意図（ボール保有時のみ有効）。
   - `shoot`: 相手ゴール中央 $(+10.0, 0.0)_{\text{rel}}$ へのシュート意図（ボール保有時のみ有効）。
   - `tackle`: 相手ボール保有者との距離がタックル半径 $r_{\text{tackle}} = 1.35$ 以内のときの奪取試行。
   - `hold`: 現在位置でのキープ/待機（移動量0）。
   - **不可能意図の扱い**: 非保有者が `pass` または `shoot` を提出した場合、例外で落とさずログに `intent_status = 'illegal_without_ball'` を記録した上で実行行動を `hold`（移動0）へ降格する。
2. **移動と能力の作用**:
   - `move` / `tackle` 選択時の1tick最大移動距離は $d_{\text{max}} = 0.55 + 0.35 \cdot \text{pace}$。ピッチ境界 $[-10.0, 10.0] \times [-6.0, 6.0]$ でクランプする。
   - `pass` 初速は $v_{\text{pass}} = 2.00 + 0.90 \cdot \text{passing}$、角度誤差は $(1.0 - \text{passing})$ に比例するキー付き一様乱数。
   - `shoot` 初速は $v_{\text{shoot}} = 2.55 + 1.05 \cdot \text{shooting}$、目標 $y$ 誤差は $(1.0 - \text{shooting})$ に比例するキー付き一様乱数。
   - 地上ボールは1tickあたり減衰係数 $\gamma_{\text{ball}} = 0.78$ で減速する。
3. **競合解決（奪取・同着・境界・得点再開）**:
   - **タックル競合**: 保有者が `hold`/`move` 中に相手が $r_{\text{tackle}} = 1.35$ 以内から `tackle` を実行した場合、挑戦者の `pace`・距離・キー付き乱数と保有者の `(pace + passing)/2`・キー付き乱数を比較し、挑戦者が上回れば奪取成功（`tackle_won`）、下回れば保有継続（`tackle_evaded`）。
   - **ルーズボール制御と同着**: ボールが非保有（`owner_id is None`）のとき、制御半径 $r_{\text{ctrl}} = 0.85$ 内の選手が捕捉候補となる。候補が1人なら単独回収（`pickup`）。複数人の場合は距離と能力・キー付き乱数から対称な捕捉スコアを計算し、最大スコアの選手が回収する。**異チーム間で最高スコアが完全一致した場合、ID順や処理順で勝者を決めず、両者の中点から反発するルーズボール（`mutual_rebound`）とする**。
   - **得点と再開**: ボールが $x \ge +10.0$ かつ $|y| \le 1.8$ を通過すれば `home` 得点、$x \le -10.0$ かつ $|y| \le 1.8$ を通過すれば `away` 得点。得点の原因（直前の `shoot` / `pass` / `move` イベントと `last_touch_id`）を必ず記録し、無原因の得点を禁止する。得点後は次tick開始時に初期配置・中央ルーズボールへ対称リセット（`kickoff_reset`）する。
   - **タッチライン/ゴールライン外**: ゴール幅外で $|x| > 10.0$ または $|y| > 6.0$ に出た場合は境界内へクランプし速度を0にして非保有化する（`out_of_bounds_stop`）。

## 主比較（4条件 × 60 seed ＝ 240 run）

同じ初期2対2フレームの上で、**実行能力・初期位置・個人Policy（スタイル）のうち1要因だけを変える介入**を設定し、40 tick × 60 seed（探索 `0..29`、確認 `100..129`、計240 run）で比較する。

| 条件 | 変更する1要因 | 初期位置 (`home`) | `home` 能力 (`pace, pass, shoot`) | `home` スタイル (`direct, pass, press`) | 観測モード |
|---|---|---|---|---|---|
| `baseline` | 対称基準（`home` と `away` が完全鏡像） | `h1=(-2.2, -1.2)`, `h2=(-3.8, 1.4)` | `h1=(0.60,0.60,0.60)`, `h2=(0.55,0.65,0.55)` | `h1=(0.55,0.50,0.50)`, `h2=(0.45,0.65,0.55)` | `local` |
| `home_skill_plus` | **実行能力のみ**（`home` 全能力 `+0.22`） | `baseline` と同一 | `h1=(0.82,0.82,0.82)`, `h2=(0.77,0.87,0.77)` | `baseline` と同一 | `local` |
| `home_position_plus` | **初期位置のみ**（`home` を $+1.8$ 前進配置） | `h1=(-0.4, -1.2)`, `h2=(-2.0, 1.4)` | `baseline` と同一 | `baseline` と同一 | `local` |
| `home_pass_style` | **個人Policyのみ**（パス志向↑・直接志向↓） | `baseline` と同一 | `baseline` と同一 | `h1=(0.25,0.88,0.50)`, `h2=(0.20,0.92,0.55)` | `local` |

- `away`（`a1`, `a2`）は全4条件で `baseline` の `home` を完全点対称 $(x, y) \mapsto (-x, -y)$ に反転した位置・能力・スタイルに固定する。
- 統計解析は対応seed単位のpaired bootstrap（2,000回、解析seed `8008`、95% percentile区間）を用い、`home_skill_plus - baseline`, `home_position_plus - baseline`, `home_pass_style - baseline` を探索・確認・全体で報告する。

## 指標（得点だけで合否を決めない）

- **行動分布**: `move`, `pass`, `shoot`, `tackle`, `hold` の選択回数と比率、および `illegal_without_ball` の発生件数。
- **空間・ボール推移**: チーム別所持tick数（`home_possession_ticks`, `away_possession_ticks`, `loose_ball_ticks`）、敵陣侵入tick数、平均ボール $x$ 座標。
- **競技イベント**: パス試行・成功数（`pass_attempts`, `pass_completions`）、タックル試行・奪取成功数（`tackle_attempts`, `tackles_won`）、シュート数・枠内シュート数・得点数（`shots`, `shots_on_target`, `goals`）。
- **判断不一致率**: `baseline` に対する各介入条件の選手別tick行動不一致率。

## 機構検査と独立対照（Probes）

| 検査 | 内容と受入基準 |
|---|---|
| 物理・会計不変条件 | 二重所持0件、座標範囲外0件、1tick最大移動距離超過0件、1人1tick 1行動厳守、無原因の得点0件。非保有者の `pass`/`shoot` は `illegal_without_ball` として記録され `hold` になる |
| 非漏洩（Non-leakage） | 同一の `LocalMatchObservation` と自分の `PlayerMatchMind` の下では、相手の真の `MatchTraits`・相手の `PlayerMatchMind`・不可視選手の真の位置をどう変えても `decide_match_player` の出力と全候補スコアが完全不変 |
| 左右反転・順序不変 | `home` と `away` の座標・能力・乱数キー役割を鏡像反転すると結果が完全鏡像一致。選手意思決定の呼び出し順や競合解決への入力順を逆転しても状態と履歴が完全一致。異チーム同着は `mutual_rebound` となる |
| 固定方針メッセージとOracle対照 | `home_directive_press`（固定方針 `'press'` 入力）でタックル/前進選択が増えること、`home_oracle_view`（視野無限・ノイズ0）で局所観測との意思決定差が生じることを確認30seedで検証 |
| 単純対照（Degenerate controls） | `always_shoot`（保有時常時シュート・非保有時直進）と `ball_camp_wait`（ボール近傍待機のみ）を `baseline` と対戦させ、一動作の反復が無条件に支配せず、`baseline` がボール支配・パス・得点差で上回ることを検証 |
| 保存・再開と破損拒否 | 4条件すべてで40 tick連続実行と `20 + 20` tick保存再開のsnapshot/JSONLがバイト一致。範囲外座標、二重所有者、改ざん判断スコア、無原因ゴール、未知version等の破損snapshotを拒否。1000 tick＋14 tick再開の実行時間・保存量を計測 |
| 旧世界・凍結成果物の保全 | T001〜T007の凍結成果物340ファイルのSHA-256が `artifacts/t008/preflight/t00*-inventory.json` と完全一致。T007の240 runの意味投影が `legacy_reference.json` と完全一致し、`artifacts/t007/source_bundle` から旧snapshotを再開可能 |
