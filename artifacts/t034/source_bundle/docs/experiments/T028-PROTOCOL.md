# T028 事前実験条件

2026-11-10、実装前登録。契約: [T028](../tasks/T028.md)。結果を見て係数・閾値・条件・seed・検定を変えない。失敗や縮退は失敗/縮退として記録し、望む結果への調整をしない。

## 固定する条件

Python 3.13.3(uv)。14世界(7条件`T011_ALL_CONDITIONS`×fixed/sampled)、seed ∈ {100,101,102}の**42セル**。主実験は`season_length_days=84`、168日(2年度)。比較は3条件: **off**(`personality="latent_v1"`のみ、`advice_response="none"` = T027と同一履歴)、**on**(`advice_response="personality_v1"`、生成された特性)、**mid**(on と同じだが、構築直後に全選手の2特性を0.5へ書換えた対照。メモリ上のみで保存しない)。on−midが特性の効果、mid−offが機構(態度の仕組みそのもの)の効果。

## 定義

態度の式・閾値・コード・イベントのキーは[契約](../tasks/T028.md)のとおり: `score=(trust−autonomy)+0.25(discipline−0.5)−0.40(1−emotional_stability)·dissatisfaction`、`score≥0`→complied、`0>score≥−0.20`→partial、`<−0.20`→refused。コードの閾値0.02。`stated_reason`は`trusts_coach`(primaryが`trust_above_autonomy`)、`distrusts_coach`(`trust_below_autonomy`)、それ以外`personal`。態度の決定時点は、選手がその日に提案を1件以上受けた練習判断の直前。

## 成功条件(ゲート)

1. **既定不変**: 既定14条件の126日digestがT023基準と一致、35日がT018と一致。`personality="latent_v1"`のみの14世界は、T027と同じ履歴(`advice_response`キーなし、イベントに`player_advice_response`/`coach_advice_outcome`なし)。`advice_response="none"`明示は既定とsnapshot同一。`decide_player_training`は`advice_mode`未指定で従来と同一の出力(合成入力で`advice_mode="blend"`明示と一致、キー集合も同一)。
2. **不正値**: `advice_response`が未知文字列・int・空文字、または`personality="none"`との組合せはValueError。
3. **純関数の性質**(`decide_advice_response`): 入力のみで決まる(同入力で同出力)、scoreは丸め後3項の和、trust・discipline・emotional_stabilityについて(他を固定して)scoreは単調非減少、dissatisfaction についてはstability<1で単調非増加、閾値境界(score=0はcomplied、score=−0.20はpartial、−0.200001はrefused)、`reason_codes`は|項|≥0.02の項のみ・降順、全て0近傍のとき`no_dominant_term`。独立に書いた表(実験側)との全格子(約10⁴点)一致。
4. **イベントの整合**(on、42セル): 全`player_advice_response`で、(a)実験側の独立実装がイベントの項から`response`・`reason_codes`・`primary_reason`を再計算して一致、(b)項の値がその日の選手の実状態(trust・autonomy・dissatisfaction)と特性から再計算した値と一致(イベント直前の状態の復元として、ステップ実行で検査)、(c)直後の`player_training_decision`が態度どおり: refusedなら全候補の`advice_term`が0かつ`score_source`がNone、compliedなら選択行動が提案方法、partialならdefault `advice_mode`での再計算と同一。(d)`coach_advice_outcome`が各応答にちょうど1つ対応し、`stated_reason`は3値、`action_followed`が実際の選択と一致。(e)提案を受けなかった選手の日には応答イベントがない。
5. **漏れ検査**(監督は結果だけを見る): (a)静的: `league_economy.py`以外のmoduleに`personality`・`emotional_stability`・`discipline`がない。`league_economy.py`で特性を読む関数(AST)は、T027の許可リストと新関数`_player_advice_response`だけ。`decide_*`(`decide_advice_response`を含む)・`decide_coach_*`・`coach_*`・監督の観測更新に`player_personality`参照がなく、`decide_advice_response`は`world`を受けずスカラー引数のみ。許可外参照を足した合成コードを検出する(感度)。(b)イベント: 全`coach_advice_outcome`のキー集合が登録どおりで、数値float値を一つも含まず、`terms`・`score`・特性名・`reason_codes`を含まない。(c)動的: 14世界(seed100)で特性を1e-9だけずらした世界(応答が一つも変わらないことを前提に確認)は、`player_personality`を除き履歴・`coach_*`状態・全イベントが完全一致。大きくずらした世界(全員0.15 / 全員0.85)では応答が実際に変わる(検出の感度: 応答イベント列がoffと異なる世界が14世界中10以上)。
6. **脚本の不在**: 応答は項の関数であることを、全イベントを項から再計算して(ゲート4a)確認。加えて、特性以外が同一の状況(同じtrust・autonomy・dissatisfaction)で特性だけ異なる選手が異なる応答を取る事例が、42セルのonのどこかに存在する(特性が実際に決定に入ったことの証拠。存在しなければ特性の効果は判定不能と書く)。
7. **再演と保存**: onの14世界(seed100、168日、`season_length_days=84`)を保存し、全再演reader・信頼readerが`_assert_same`で一致、manifest付き。介入1件を挟む分岐(30日→fork→+60日)が全再演readerで一致。ステップ実行と一括実行が一致。(`aging`・`season`併用: 2世界で`season_length_days=42`+`aging_start_age=29`+`aging_curve`併用でも往復一致)。改ざん: `player_advice_response`の`response`変更、`coach_advice_outcome`の削除は全再演readerが拒否する。信頼readerは記録(イベント)の削除を見逃す既知の限界(T022)があるため、信頼reader+manifest偽造の検出は主張せず、結果を記述として報告する。
8. **検査**: 全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t028/preflight/historical-inventory.json`)のhash不変、source_bundle保存。

## 測定(仮説と予測)

onの42セルの全応答(各セル168日)を集計する。

- **P1 規律性と従う割合**(機構の検証): プールした応答で、complied割合が`discipline`の三分位(<0.3833 / 0.3833–0.6167 / >0.6167)の低<中<高の順に単調。セル別では、高三分位−低三分位の符号が正のセル数を記述。
- **P2 感情安定性と不満時の拒否**: 不満足(dissatisfaction>0.10)の応答に限ったrefused割合は、`emotional_stability`三分位の低≥高(プール)。
- **P3 縮退しない**: プール応答でcomplied・partial・refusedの各割合が5%以上。いずれかが5%未満なら「分布が縮退」と記録し、係数は変えない。
- **P4 結果への効果(方向は予測しない)**: 各セルで168日時点の(i)全選手の平均真の技能(`composite_skill`)、(ii)平均`playing_time_satisfaction`、(iii)全選手×全監督の平均trust、について on−mid(特性の効果)と mid−off(機構の効果)の対応差。報告は平均差、符号がプラスのセル数/42、片側でない厳密な符号検定のp値(同値を除く)。主検定は 3指標×2比較=6 本、有意水準 0.05/6=0.0083。**効果が有意でなければ「検出できなかった」と書き、特性が成長に効くとは言わない。** 84日時点も同じ指標を記述のみで報告(検定外)。
- **P5 探索(記述のみ)**: onの各セル内の、選手の`discipline`と(168日の技能−初期技能)のSpearman順位相関、`emotional_stability`と168日の平均満足度のSpearman順位相関の、セルの中央値と符号セル数。因果とは呼ばない。
- 記述: 応答の割合(全体、特性三分位別)、`primary_reason`の頻度、`stated_reason`の頻度、監督別・条件別の応答割合、onとoffの完了移籍件数・イベント数の差。

## 失敗条件

1〜8のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の係数・閾値・特性・検定の変更、監督が特性または項の数値を読めてしまうこと。P1〜P5の予測が外れても、それは結果であって失敗ではない(外れた場合は仮説の反証/判定不能として記録する)。

## 限界

係数(0.25, 0.40)・閾値(0, −0.20)・式の形は未校正の仮説で、現実の従順の心理モデルではない。助言の質(監督の予測の正しさ)は別問題で、従うことが得とは限らない。応答の対象は最も信頼する監督の提案1件で、複数監督の個別態度は扱わない。態度は同日の練習判断にだけ効き、満足度・信頼への反応の大きさは既存の更新式(特性なし)のまま。`emotional_stability`は態度の式の不満項の大きさだけを変える。監督は結果を観測するが、まだそれを使わない。3seedはセル間で独立ではなく(同じ世界条件を共有)、符号検定は参考値。
