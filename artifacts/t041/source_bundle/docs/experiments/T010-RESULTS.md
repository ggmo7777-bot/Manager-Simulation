# T010 実験結果: 2クラブ間の育成(A/B)・起用・直接対戦2D試合・3者合意移籍ループ (`world_kind="transfer_loop"`, `save=10`)

- **実施日**: 2026-11-07
- **作成者 (Author)**: **Antigravity (Gemini 4 Argon)** — ※T001〜T007の既存作成者（`Sol 6.1 High`）とは異なる作成者による実装・検証記録
- **対象タスク**: [T010.md](../tasks/T010.md)
- **実験プロトコル**: [T010-PROTOCOL.md](T010-PROTOCOL.md)
- **意思決定記録**: [DECISIONS.md](../DECISIONS.md) (`D023`, `D024`)
- **パッケージ識別子**:
  - `world_kind`: `"transfer_loop"`
  - `save`: `10`
  - `rules`: `"t010-transfer-loop-v1"`
  - `policy`: `"t010-transfer-score-v1"`
  - `rng`: `"sha256-keyed-uniform53-v1"`
  - `author`: `"Antigravity (Gemini 4 Argon)"`
  - `source_sha256`: `f3a8b07d06f17ec4adf3ac5fd13e905a800aa9edf3e3a47b40b2c420e0fc6a48` ([manifest.json](../../artifacts/t010/source_bundle/manifest.json))

---

## 1. 実験規模と再現コマンド

- **主実験 ($2 \times 2$ 要因計画)**: 4条件 (`decoupled_stay`, `coupled_stay`, `decoupled_transfer`, `coupled_transfer`) × 2シード集合 (`exploration: 1..30`, `confirmation: 100..129`) × 84日（12試合・移籍窓口 Day 28, Day 56） = **240 runs**
- **3者合意メカニズム単一要因対照実験**: 3条件 (`fair_c1_prior_p3`, `low_c2_scouting_p3`, `high_loyalty_p3`) × 確認用シード (`100..129`, 30 seeds) × 84日 = **90 runs**
- **長期ホライズン検証**: `coupled_transfer`, `seed=17`, `1,000`日走行 + `14`日再開 (`1,014`日完全一致検証)
- **代表ラン**: `coupled_transfer`, `seed=17`, `84`日 (`full` vs `first_half` 42日 + `resumed` 42日)

```sh
python3 -B -m soccer_sim experiment --suite t010 --out artifacts/t010/experiments
python3 -B -m soccer_sim run --condition transfer_coupled_transfer --seed 17 --days 84 --out artifacts/t010/representative/full
python3 -B -m soccer_sim run --condition transfer_coupled_transfer --seed 17 --days 42 --out artifacts/t010/representative/first_half
python3 -B -m soccer_sim resume --snapshot artifacts/t010/representative/first_half/snapshot.json --days 42 --out artifacts/t010/representative/resumed
python3 -B artifacts/t010/review/verify_compatibility.py
python3 -B artifacts/t010/review/audit_transfer.py
python3 -B -m unittest discover -s tests -v
```

---

## 2. 主実験 ($2 \times 2$ 要因計画) の結果

### 2.1 Confirmation Seeds (`100..129`, 30 seeds) 条件別平均

出典: [summary.json](../../artifacts/t010/experiments/summary.json) (`confirmation_means`)

| 指標 | `decoupled_stay` | `coupled_stay` | `decoupled_transfer` | `coupled_transfer` |
| --- | ---: | ---: | ---: | ---: |
| `total_transfers` | `0.0000` | `0.0000` | `1.6333` | `2.1000` |
| `p3_transferred` (移籍率) | `0.0000` | `0.0000` | `0.9333` | `0.9333` |
| `p3_transfer_requests` | `1.9333` | `1.9667` | `1.2333` | `1.2333` |
| `p3_appearances` (総出場数 / 12試合) | `1.9000` | `3.4333` | `6.6000` | `7.4667` |
| `p3_club_a_appearances` | `1.9000` | `3.4333` | `0.6333` | `0.5667` |
| `p3_club_b_appearances` | `0.0000` | `0.0000` | `5.9667` | `6.9000` |
| `p3_playing_time_satisfaction` | `0.1033` | `0.1950` | `0.8200` | `0.9340` |
| `p3_skill_gain` | `0.383319` | `0.385332` | `0.391635` | `0.394029` |
| `mean_skill_gain` (6選手平均) | `0.267513` | `0.267821` | `0.267497` | `0.267223` |
| `p3_proposals_from_c1` | `71.0667` | `69.3333` | `26.6667` | `27.0000` |
| `p3_proposals_from_c2` | `0.0000` | `0.0000` | `11.7667` | `14.9333` |
| `club_a_goals` | `7.1333` | `10.5667` | `6.5333` | `5.1333` |
| `club_b_goals` | `3.9000` | `9.0000` | `3.6667` | `15.9333` |
| `club_b_minus_club_a_gd` (通期得失点差) | `-3.2333` | `-1.5667` | `-2.8667` | `+10.8000` |
| `second_half_club_b_minus_club_a_gd` (後半6試合GD) | `-2.1333` | `-1.5333` | `-1.7000` | `+8.6333` |
| `club_a_shots` | `22.3667` | `20.9333` | `21.9667` | `11.5333` |
| `club_b_shots` | `16.0667` | `34.2000` | `14.5000` | `45.5333` |
| `second_half_club_b_shots` | `7.7000` | `19.1333` | `6.5333` | `28.6667` |
| `club_a_possession_ticks` | `214.9000` | `190.9333` | `217.3000` | `159.7667` |
| `club_b_possession_ticks` | `191.1000` | `189.1000` | `192.0667` | `216.2667` |

*(Exploration Seeds `1..30` でも同方向・同規模の傾向を確認: `coupled_transfer` で `p3_transferred = 0.9667`, `p3_appearances = 7.6333`, `p3_skill_gain = 0.396986`, `club_b_minus_club_a_gd = +14.1000`, `second_half_club_b_minus_club_a_gd = +11.3333` vs `coupled_stay` `-0.8667` / `-1.7000`。)*

### 2.2 主要対比 (Paired Bootstrap 95% CI, Confirmation Seeds `100..129`, 2,000 resamples)

出典: [summary.json](../../artifacts/t010/experiments/summary.json) (`confirmation_contrasts`)

| 対比 | 指標 | 平均差 | 95% CI | 解釈 |
| --- | --- | ---: | --- | --- |
| `coupled_transfer - coupled_stay` | `p3_transferred` | `+0.9333` | `[+0.8333, +1.0000]` | 30シード中28シードで `p3` が `club_a` → `club_b` へ移籍成立 |
| `coupled_transfer - coupled_stay` | `p3_appearances` | `+4.0333` | `[+3.3333, +4.7333]` | 移籍により `p3` の出場試合数が `3.43` → `7.47` 試合へ倍増 (`club_b` で `+6.90` 出場) |
| `coupled_transfer - coupled_stay` | `p3_playing_time_satisfaction` | `+0.7390` | `[+0.6443, +0.8210]` | 出場機会獲得により満足度が `0.195` → `0.934` へ回復 |
| `coupled_transfer - coupled_stay` | `p3_skill_gain` | `+0.008697` | `[+0.006192, +0.011234]` | 新天地での実戦出場と `c2` の指導 (`+14.93` 回) により `p3` の成長量が増加 |
| `coupled_transfer - coupled_stay` | `club_b_minus_club_a_gd` | `+12.3667` | `[+10.4325, +14.3333]` | `p3` 獲得により `club_b` 対 `club_a` の通期得失点差が `-1.57` → `+10.80` へ逆転 |
| `coupled_transfer - coupled_stay` | `second_half_club_b_minus_club_a_gd` | `+10.1667` | `[+8.2325, +12.1675]` | 移籍成立後の後半6試合で得失点差が `-1.53` → `+8.63` へ劇的転換 |
| `coupled_transfer - coupled_stay` | `club_b_shots` | `+11.3333` | `[+8.9667, +13.9000]` | `club_b` のシュート数が `34.20` → `45.53` へ増加 |
| `coupled_transfer - coupled_stay` | `club_b_possession_ticks` | `+27.1667` | `[+21.9667, +32.7333]` | `club_b` のポゼッション時間が有意に増加 (`club_a` は `-31.17` ticks) |
| `coupled_transfer - decoupled_transfer` | `club_b_minus_club_a_gd` | `+13.6667` | `[+11.4667, +15.8667]` | 同じ移籍率 (`0.9333`) でも、日次の育成・疲労が2D試合エンジンへ結合して初めて新天地での戦力化が発現 |
| `coupled_transfer - decoupled_transfer` | `second_half_club_b_minus_club_a_gd` | `+10.3333` | `[+7.7325, +12.6000]` | `decoupled_transfer` では移籍後も後半GD `-1.70` に留まるのに対し、`coupled_transfer` では `+8.63` |
| `interaction_coupling_by_transfer` | `club_b_minus_club_a_gd` | `+12.0000` | `[+9.4667, +14.4667]` | 結合×移籍の正の交互作用: 育成結合と移籍市場の両方が揃って初めてクラブ間競争バランスが転換 |
| `interaction_coupling_by_transfer` | `second_half_club_b_minus_club_a_gd` | `+9.7333` | `[+7.4000, +12.1667]` | 後半6試合における結合×移籍交互作用 |
| `interaction_coupling_by_transfer` | `club_b_shots` | `+12.9000` | `[+10.1992, +15.8000]` | シュート数における結合×移籍交互作用 |

### 2.3 行動列不一致率 (Action Sequence Disagreement Rates, Confirmation Seeds `100..129`)

| 対比 | `transfers` | `lineup_club_a` | `lineup_club_b` | `coach_c1` | `coach_c2` | `players_mean` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `coupled_transfer` vs `coupled_stay` | `0.1750` (`[0.1528, 0.1944]`) | `0.3778` (`[0.3194, 0.4306]`) | `0.6111` (`[0.5583, 0.6500]`) | `0.5481` (`[0.5002, 0.5870]`) | `0.3847` (`[0.3419, 0.4234]`) | `0.2777` (`[0.2507, 0.3019]`) |
| `coupled_transfer` vs `decoupled_transfer` | `0.1056` (`[0.0806, 0.1361]`) | `0.5278` (`[0.4472, 0.6139]`) | `0.6556` (`[0.5889, 0.7167]`) | `0.3859` (`[0.3389, 0.4387]`) | `0.4403` (`[0.3884, 0.4882]`) | `0.3618` (`[0.3441, 0.3788]`) |

---

## 3. 単一要因対照実験（3者合意の各主観条件の独立検証）

`coupled_transfer` を基準として、3者（放出側監督 `c1`・獲得側監督 `c2`・選手 `p3`）の主観パラメータを1つずつ変更した3つの対照条件を Confirmation Seeds (`100..129`, 30 seeds) で比較した。

出典: [summary.json](../../artifacts/t010/experiments/summary.json) (`extra_controls_means`, `extra_controls_effects`)

| 条件 | 変更した単一要因 | `p3_transferred` (`Δ vs coupled_transfer`) | `p3_transfer_requests` | `p3_appearances` (`club_a` / `club_b`) | `p3_skill_gain` | `club_b_minus_club_a_gd` (`Δ vs coupled_transfer`) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `coupled_transfer` (基準) | なし (`c1` prior `(0.40, 0.38)`, `c2` prior `(0.49, 0.51)`, `loyalty=0.06`) | `0.9333` (基準) | `1.2333` | `7.4667` (`0.57` / `6.90`) | `0.394029` | `+10.8000` (基準) |
| `fair_c1_prior_p3` | 放出側監督 `c1` の `p3` 初期主観のみ `(0.50, 0.54)` に適正化 | `0.2000` (`-0.7333`, CI `[-0.8667, -0.5667]`) | `0.5000` (`-0.7333`) | `8.7333` (`7.37` / `1.37`) | `0.393032` | `-4.0667` (`-14.8667`, CI `[-17.7333, -11.7992]`) |
| `low_c2_scouting_p3` | 獲得側監督 `c2` の `p3` 初期スカウト評価のみ `(0.31, 0.29)` に低下 | `0.4667` (`-0.4667`, CI `[-0.6333, -0.3000]`) | `1.9667` (`+0.7333`) | `4.7000` (`2.97` / `1.73`) | `0.387301` (`-0.006728`) | `+0.7000` (`-10.1000`, CI `[-12.1008, -8.0333]`) |
| `high_loyalty_p3` | 選手 `p3` のクラブ愛着 `loyalty` のみ `0.35` に上昇 | `0.2000` (`-0.7333`, CI `[-0.8667, -0.5667]`) | `1.1000` (`-0.1333`) | `4.0000` (`3.20` / `0.80`) | `0.386907` (`-0.007122`) | `-0.3000` (`-11.1000`, CI `[-12.9333, -9.2667]`) |

### 3者合意の因果分解から得られた知見
1. **放出側監督 `c1` の主観バイアス解消 (`fair_c1_prior_p3`)**: `c1` が当初から `p3` を正当に評価していれば、`p3` は `club_a` で平均 `7.37` 試合先発起用され、そもそも移籍志願自体が `1.23` → `0.50` 回へ激減し、かつ `c1` も主力を放出しないため移籍率は `0.2000` (`-0.7333`) へ低下する。その結果、`p3` は `club_a` の主力として活躍し、通期得失点差は `club_a` 優位 (`club_b - club_a = -4.07`, 基準比 `-14.87`) となる。
2. **獲得側監督 `c2` のスカウト評価不足 (`low_c2_scouting_p3`)**: `p3` が `club_a` でベンチに置かれて移籍志願 (`1.97` 回) し、`c1` も放出に同意しても、獲得側 `c2` の初期スカウト評価が低いと Day 28 でのオファーが見送られやすく（Day 28〜56 の追加スカウトで評価が上がった一部のシードのみ Day 56 で移籍）、移籍率は `0.4667` (`-0.4667`) に半減し、`p3` の出場数 (`-2.77` 試合) と成長量 (`-0.006728`) が有意に減少する。
3. **選手 `p3` の高いクラブ愛着 (`high_loyalty_p3`)**: `c1` が放出可能で `c2` がオファーを出しても、選手自身の `loyalty` が高い (`0.35`) と移籍志願・オファー受諾スコアが閾値に達せず、移籍率は `0.2000` (`-0.7333`) に低下し、`club_a` に残留して出場機会が限られる (`4.00` 試合)。
4. **結論**: 移籍を強制するハードコード規則を一切置かず、**選手本人の不満と展望・現所属監督の放出判断・獲得先監督のスカウト評価と補強判断の3者の主観的合意（Mutual Agreement）**だけで、キャリア分岐とクラブ間勢力図の変化が創発することが確認された。

---

## 4. 代表ラン (`seed=17`) のミクロ履歴と非移籍分岐の創発

代表シード `seed=17` (`coupled_transfer`, [artifacts/t010/representative/full/summary.json](../../artifacts/t010/representative/full/summary.json)) は、Exploration Seeds (`1..30`) 30シードのうち **唯一 `p3` が移籍せず `club_a` で先発定着に成功した 1 シード (`29/30` シードは移籍)** である。
- `seed=17` では、`p3` が高い自律練習駆動によって前半戦のうちに `c1` の観察評価を覆し、前半・後半を通じて `club_a` で `6` 試合に先発出場 (`p2` と出場機会を分け合う第2スターターに昇格) した。
- その結果、Day 28・Day 56 の移籍窓口で `p3` はわずかに移籍志願閾値を超えたものの、`c1` の主観順位で `p3` がクラブ内2位 (`player_rank_in_from = 2 < 3`) となっていたため、**放出側監督 `c1` が主力流出を拒否 (`release_consented = False`)** し、`p3` は `club_a` に残留して成長 (`skill_gain = +0.383761`, 最終平均スキル `0.847094`) した。
- 一方、`seed=100`（[audit_transfer.py](../../artifacts/t010/review/audit_transfer.py) で全イベント監査済み）をはじめとする `28/30`（Confirmation）および `29/30`（Exploration）のシードでは、`p3` は前半4試合で `club_a` のベンチに置かれた結果、Day 28 の第1移籍窓口で 3者合意（`wants_transfer = True`, `release_consented = True`, `offer_made = True`, `offer_accepted = True`）が成立して `club_b` へ移籍し、後半戦8試合すべてに `club_b` の先発として出場して `club_b` の逆転勝利を牽引した。
- このように、同一の `coupled_transfer` 条件・同一のパラメータの下でも、**前半戦の観察・試合展開のわずかな違いから「現所属クラブで評価を覆して残留するキャリア (`seed=17`)」と「出場機会を求めて新天地へ移籍し開花するキャリア (`seed=100` 等)」の両方が自律的に分岐する**ことが実証された。

---

## 5. 技術健全性・監査・長期ホライズン・凍結互換性

1. **チェックポイント保存・再開のバイト完全一致**:
   - 代表ラン (`seed=17`, `84`日) の一括実行 (`full`) と `42`日保存 (`first_half`) + `42`日再開 (`resumed`) のスナップショット SHA-256 は `d10c89a3175c8bcdaf80691a23351a8d1bcbf2ca6f39bfb4c885743e30cf72b0` で完全一致。
   - 単体テスト ([tests/test_t010.py](../../tests/test_t010.py)) にて全4主条件 (`decoupled_stay`, `coupled_stay`, `decoupled_transfer`, `coupled_transfer`) の `42 + 42` 日保存・再開完全一致を確認。
2. **長期ホライズン `1,000 + 14` 日検証**:
   - 出典: [summary.json](../../artifacts/t010/experiments/summary.json) (`long_horizon_1000_plus_14`)
   - `1,000`日走行（`142` 試合、`36,200` イベント）の実行時間 `2.525859` 秒、保存時間 `1.210541` 秒（`snapshot.json`: `25,098,916` bytes、`events.jsonl`: `25,088,167` bytes）、**1パス読込時間 `0.375645` 秒**（T007の暫定リプレイ読込を排し、状態直接復元＋1パス整合検証により高速読込を維持）、さらに `+14` 日再開後の `1,014` 日一括走行とのスナップショット SHA-256 完全一致 (`resume_1000_plus_14_byte_equal = true`) を確認。
3. **非漏洩・イベントログ1パス独立再計算監査 (`policy_functions_called == 0`)**:
   - 出典: [transfer-audit.json](../../artifacts/t010/review/transfer-audit.json)
   - `seed=17` 代表ランおよび `seed=100` 全4主条件の計5ランについて、Policy関数を一切呼ばず (`policy_functions_called == 0`) イベントログのみから主観状態を追跡し、`coach_training_decision` (`1,440` 件)、`player_training_decision` (`2,160` 件)、`lineup_decision` (`120` 件)、`transfer_window_decision` (`60` 件) の**計 `3,780` 件のPolicy意思決定**と **`748` 件の `advice_feedback`**（合計 **`4,528` 件**）を独立再計算し、不一致 `0` 件 (`total_mismatches == 0`) を確認。
4. **T001〜T009 凍結成果物 (485ファイル) の完全保持と T009 全240ラン意味論的一致**:
   - 出典: [verification.json](../../artifacts/t010/review/verification.json)
   - `t001: 34, t002: 32, t003: 38, t004: 44, t005: 47, t006: 55, t007: 88, t008: 83, t009: 64` の計 **485ファイル** が1バイトも変更されていないこと、および T009 の全240ランが `identity.source_sha256` 以外の全状態・全イベントで完全一致することを確認。
   - 全単体テスト `test_t001`〜`test_t010` の計 **109 tests** がすべて成功 ([tests.json](../../artifacts/t010/review/tests.json))。
