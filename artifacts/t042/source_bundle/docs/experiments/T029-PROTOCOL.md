# T029 事前実験条件

2026-11-10、実装前登録。契約: [T029](../tasks/T029.md)。結果を見て係数・閾値・条件・seed・検定を変えない。失敗や縮退は失敗/縮退として記録し、望む結果への調整をしない。

## 固定する条件

Python 3.13.3(uv)。14世界(7条件`T011_ALL_CONDITIONS`×fixed/sampled)、seed ∈ {100,101,102}の**42セル**、`personality="latent_v1"`+`advice_response="personality_v1"`+`season_length_days=84`、168日(T028のonと同じ基盤)。3モード: **base**(`coach_belief="none"` = T028のonと同一履歴)、**observe**(`observe_v1`)、**use**(`use_v1`)。

## 定義

信念の更新・信号・減衰0.97・事前(1,1)・利用`uptake=min(1,mean/0.5)`は[契約](../tasks/T029.md)のとおり。研究者のみが見る「真の現在の受容度」`s_now`: 168日時点で、(監督, 選手)が現在同じクラブの組について、選手の真の状態(その監督へのtrust・autonomy・discipline・emotional_stability・満足度)を`decide_advice_response`に入れた応答の信号(complied=1、partial=0.5、refused=0)。**監督・Policy・信念には渡さない**。信念との比較は実験側の事後計測のみ。

## 成功条件(ゲート)

1. **既定不変**: 既定14条件の126日digestがT023基準、35日がT018と一致(T028のcheckを再利用)。`coach_belief="none"`明示の14世界は既定とsnapshot同一(`advice_response`有効の世界も含む)。T028のonの42セルを`coach_belief="none"`で再実行した168日時点の指標(平均技能・満足度・trust・完了移籍・イベント数)が、凍結した`artifacts/t028/run/summary.json`のon値と**完全一致**。`decide_coach_training`は`receptiveness`未指定で従来と同一出力(合成入力と実worldの全日の呼出しで比較、キー集合も同一)。
2. **不正値**: `coach_belief`が未知文字列・int・空文字、または`advice_response="none"`との組合せはValueError。`has_save_fields`は有り/無しを受理し未知キーを拒否。
3. **純関数の性質**(`update_receptiveness_belief`、`uptake`): 実験側の独立実装と全列(信号3種×理由3種の長さ≤6の全系列、約10³本)で一致。平均はcomplied後に増え、refused後に減り、partial(信号0.5)で0.5へ向かう。`action_followed`など他キーを変えても出力不変(`response`と`stated_reason`以外を読まない)。`sd`は(0, 0.2887]。`n`は更新回数。`uptake`は`mean≥0.5`で1、`mean<0.5`で`mean/0.5`、0以上1以下で単調非減少。
4. **イベント整合**(observe・useの84セル): (a)全`coach_advice_outcome`の直後にちょうど1つの`coach_belief_update`(cause_refs=[そのoutcome]、キー集合は登録どおり)、(b)実験側がイベント列だけから独立に畳み込んだ信念が`world.coach_beliefs`と全(監督,選手)で一致、(c)各イベントの`mean/sd/n`がその時点の畳み込み値と一致、(d)信念のキーは`coach_advice_outcome`が存在した組のみ。
5. **利用は信念の平均だけを使う**(useの42セル): 全`coach_training_decision`で、propose候補の`components.uptake`が「その時点の畳み込み済み信念の平均」(信念が無ければ0.5)から`min(1,mean/0.5)`と一致し、候補スコアが`0.15+need_term+forecast_term+pref_bonus+fatigue_penalty+repeat_penalty`(各項はイベントの値)で再計算できる。observe候補・scout候補・rest候補・observeモードの全候補にuptakeが無く、base(信念なし)と同じ構成。
6. **observeは判断を変えない**(42セル): observeの履歴は、`coach_belief_update`を除きIDと原因参照を詰め直す(除いたイベントへの参照はそのcause=直前のoutcomeへ付け替える。T025の補正A1と同じ事情)と、baseの履歴・summary・最終状態(`coach_beliefs`を除く)と完全一致。
7. **漏れ検査**(信念は結果だけを見る): (a)静的(AST): `league_economy.py`で特性を読む関数は許可リスト(T027+`_player_advice_response`)のまま増えない。`update_receptiveness_belief`・`_beliefs_from_events`・`_coach_receptiveness`・`decide_coach_training`・`decide_advice_response`以外の信念関数に、`player_personality`・`personality`・`discipline`・`emotional_stability`・`player_minds`・`players`・`initial_players`・`trust_by_coach`・`autonomy`・`playing_time_satisfaction`への参照がない。許可外参照を足した合成コードを検出する(感度)。(b)入力検査: 実行中に`update_receptiveness_belief`へ渡った引数のキー集合が全回`coach_advice_outcome`の登録キー集合と一致し、`player_advice_response`の項・特性値は一つも渡らない。(c)動的(14世界、useモード、seed100): 特性を1e-9だけずらしても応答が変わらなければ、`player_personality`と`player_advice_response`の項・scoreを除く履歴・`coach_beliefs`・全イベントが完全一致。全員0.15/全員0.85では信念の列がbaseのonと異なる(14世界中10以上=特性が結果を通してのみ信念に入る感度)。
8. **再演と保存**: use(およびobserve)の14世界(seed100、168日、年度長84)が全再演reader・信頼readerで一致。信頼readerは`coach_beliefs`を、イベントからの畳み込みと照合して改ざんを拒否。分岐(30日→fork→+60日)が一致、ステップ実行=一括実行。年度42+加齢併用の2世界の往復一致。改ざん: `coach_beliefs`の値変更、`coach_belief_update`の削除、`coach_advice_outcome`の`response`変更は全再演readerが拒否。信頼readerが`coach_beliefs`変更を拒否することを確認。信頼readerがイベント内容の改ざんをmanifest偽造後に受理する既知の限界(T022)は主張せず記述。
9. **検査**: 全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t029/preflight/historical-inventory.json`)のhash不変、source_bundle保存。

## 測定(仮説と予測)

useとobserveの42セルで、研究者のみが真値と比較する。

- **P1 信念は現在の受容度を追う**(observe): 168日の(監督,選手)で`n≥5`かつ同クラブの組の信念平均と`s_now`のSpearman順位相関をセルごとに求め、中央値>0.30かつ正のセル≥30/42と予測。
- **P2 定数より良い**(observe): 同じ組で`|mean−s_now|`の平均が、定数0.5(事前)の平均より小さいセルが≥30/42と予測。
- **P3 特性の符号化は結果の範囲**(記述+予測): 信念平均と真の`discipline`/`emotional_stability`のSpearman順位相関(セル中央値)を記述する。信念が特性を直接見ていないことは漏れ検査7で保証される。この相関は結果経由の弱い相関にとどまると予測(|中央値|<0.30)。
- **P4 機構**(use対observeの対応差): (i)コーチがpropose候補を選ぶ割合、(ii)選手が受けた助言の応答件数、は減ると予測(use<observe、片側でない符号検定、各セル)。(iii)応答のうちrefusedの割合は低下すると予測。方向が外れたら予測の反証として記録。
- **P5 結果への効果(方向は予測しない)**: 各セルの168日時点で、use−observeの対応差(平均真の技能、平均満足度、全選手×全監督の平均trust)について平均差、正の数、片側でない厳密な符号検定のp値(同値を除く)。主検定は3本、有意水準0.05/3=0.0167。**有意でなければ「検出できなかった」と書き、受容度の利用が成長に効くとは言わない。** 84日時点は記述のみ。
- 記述: 応答の割合(observe/use)、信念の平均・sdの分布、`uptake<1`の提案候補の割合、使われた`uptake`の分布、完了移籍件数・イベント数の差、`stated_reason`の累計(`reasons`)、信念が下がり切った組(平均<0.2)で提案が止まったか(再び提案される組の割合)。

## 失敗条件

1〜9のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の係数(0.97、(1,1)、0.5、信号値)・検定の変更、信念が特性・項の数値・真の状態を入力に持つこと。P1〜P5の予測が外れても、それは結果であって失敗ではない(外れた場合は仮説の反証/判定不能として記録する)。

## 限界

信念の更新式・減衰・信号値・利用式(uptake)は未校正の仮説。受容度は最も信頼する監督の1件の助言に対する応答の移動平均で、因果的な「性格の推定」ではない。信念は監督×選手の対ごとで、監督間では共有されない。表明理由は回数として記録するだけで判断には使わない。`use_v1`では受容度が低いと提案が減り、新しい観測が得られず信念が更新されなくなる探索の問題が起こり得る(探索ボーナスは範囲外)。3seedはセル間で独立ではなく(同じ世界条件を共有)、符号検定は参考値。
