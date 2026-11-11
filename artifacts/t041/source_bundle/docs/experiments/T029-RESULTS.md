# T029 結果 — 監督の受容度の信念(B層)と、提案判断での最小利用

実装・実験: 2026-11-10。契約: [T029](../tasks/T029.md)、事前条件: [PROTOCOL](T029-PROTOCOL.md)、判断: [D044](../DECISIONS.md)。信念の式・減衰・信号値・利用式・条件・検定は実装前にコミット(4c2d4b0)し、結果後に変えていない。Python 3.13.3(uv)、macOS arm64。**式・係数は未校正の仮説。**

## 結論

- `LeagueEconomyConfig.coach_belief`(`"none"`既定、`"observe_v1"`、`"use_v1"`、`advice_response`必須)を追加。監督は`coach_advice_outcome`(態度・3値の表明理由)**だけ**から、監督×選手ごとの受容度の信念をBeta疑似回数((1,1)事前、減衰0.97、信号 従う1/部分0.5/拒否0)で更新し、平均・標準偏差・観測数・表明理由の回数を持つ。更新ごとにイベント`coach_belief_update`を記録。信念は結果イベント列の純粋な畳み込みで、不変条件がイベントから再計算して一致を要求する。Cの方針学習はしない。
- **判断での利用は最小**: `use_v1`のみ、提案(propose)候補の`need_term`・`forecast_term`に`uptake=min(1,mean/0.5)`を掛ける。観察・偵察・休養と`observe_v1`の判断は不変(`observe_v1`は信念を記録するだけ)。
- 成功条件1〜9を満たした(下表)。予測P1・P2・P3は支持、P4(機構)は3つとも支持。P5(結果)は有意だが小さい**悪化**(技能−0.0017、trust−0.0090)で満足度は無差。受容度の利用が成長に効くとは言えない。

## ゲート

| 条件 | 結果 |
|---|---|
| 1 既定不変 | 既定14条件の126日digestがT023基準、35日がT018と一致。`coach_belief="none"`明示の14世界(advice有効)は既定とsnapshot同一。`coach_belief="none"`の42セル(168日・84日)の指標が、凍結した`artifacts/t028/run/summary.json`のon値と**完全一致**(42/42)。`decide_coach_training`は`receptiveness`未指定=`None`で従来と同一(`{}`を渡すと`uptake`キー以外が同一) |
| 2 不正値 | 未知文字列・int・空文字・`advice_response="none"`との組合せの4件ともValueError。`has_save_fields`は有り/無しを受理し未知キーを拒否 |
| 3 純関数 | 4,095系列(応答3種×一定の表明理由3種の長さ≤6の3,276本+9記号の長さ≤3の819本)で実験側の独立実装と完全一致。平均はcomplied後に増加、refused後に減少、partial後は0.5へ近づく。`sd`は(0,0.2887]、`n`は更新回数。`response`・`stated_reason`以外のキー(`action_followed`等)を変えても出力不変。`uptake`は101点で独立実装と一致・単調非減少・0.5以上で1 |
| 4 イベント整合 | observe・use各42セル(計84)で、全`coach_advice_outcome`の直後にちょうど1つの`coach_belief_update`(cause=そのoutcome、キー集合は登録どおり、`mean/sd/n`は独立畳み込みと一致)。最終`coach_beliefs`は独立畳み込みと一致 |
| 5 利用は信念の平均だけ | use全42セルの全`coach_training_decision`(propose候補計290,304件)で、渡された`receptiveness`が独立畳み込みの平均と一致、`uptake`が`min(1,mean/0.5)`と一致、`need_term`・`forecast_term`が`uptake`なしの再計算値×`uptake`(±2e-6)、候補スコアが成分から再計算できる。observe/scout/rest候補は`uptake`なしの再計算と同一 |
| 6 observeは判断を変えない | observeの42セルすべてが、`coach_belief_update`を除きIDと原因参照を詰め直す(除いたイベントへの参照は直前のoutcomeへ。`obs_refs`・それを写した`cause_refs`・`transfer_offer_requested`の`cause_refs`は文字列順のsorted集合なので、番号の桁が変わると順序が変わる。そのため詰め直し後に再ソートして比較)と、base(`coach_belief="none"`)のsnapshotと完全一致 |
| 7 漏れ検査 | 静的: 特性を読む関数は許可リストのまま(T028の検査を再実行)。`update_receptiveness_belief`・`_beliefs_from_events`・`_coach_receptiveness`・`decide_coach_training`・`belief_mean_sd`・`new_receptiveness_belief`・`receptiveness_uptake`は、特性・`player_minds`・`players`・`trust_by_coach`・`autonomy`・`playing_time_satisfaction`等に触れない(AST)。`update_receptiveness_belief`の引数は`(belief, outcome)`で、参照するキーは`response`・`stated_reason`のみ。許可外参照を足した合成コードを検出。入力: 実行中に`update_receptiveness_belief`へ渡った引数のキー集合が全回`coach_advice_outcome`の登録キー集合と一致。動的(14世界、use、seed100): 特性を1e-9ずらしても応答が同じなら履歴・`coach_beliefs`・全イベントが完全一致、全員0.15/全員0.85では14世界すべてで信念が変わる(基準10以上) |
| 8 再演と保存 | observe・use各14世界(seed100、168日、年度84)が全再演・信頼の両readerで一致。分岐(30日→fork→+60日)・ステップ実行=一括実行が一致。年度42+加齢併用2世界も往復一致。改ざん3種(`coach_beliefs`の値変更、`coach_belief_update`削除、`coach_advice_outcome`の`response`変更)を、**全再演readerも信頼readerも(manifest偽造後でも)拒否**。信頼readerが信念をイベントから再計算するため、T028では受理されたoutcomeの`response`変更も拒否された。ただし信念と矛盾しない形のイベント改ざんを信頼readerが見逃し得る既知の限界(T022)は主張しない |
| 9 検査 | 全unittest合格(`artifacts/t029/tests-all.txt`)、旧成果物4,801ファイルのhash不変(`preflight/inventory-verify.json`)、source_bundle保存 |

## 測定(42セル、168日)

- **P1(支持)**: 168日で`n≥5`かつ現在同クラブの(監督,選手)(1セル平均約8.5組)の信念平均と、研究者のみが計算する真の現在の受容度`s_now`のSpearman順位相関は、セル中央値**+0.83**、正のセル40/40(2セルは`s_now`が一定で相関なし)。
- **P2(支持)**: 平均絶対誤差は信念0.103、定数0.5が0.326。信念が近いセルは42/42。
- **P3(支持、予測内)**: 信念平均と真の`discipline`の相関はセル中央値+0.18、`emotional_stability`は−0.17(いずれも|0.30|未満)。信念は特性を直接見ない(漏れ検査7)ので、これは結果を経由した弱い相関で、特性の復元ではない。1標準偏差内に真値がある割合は中央値0.25(sdは疑似回数が多く小さいため、離散の`s_now`(0/0.5/1)に対して過信気味。記述のみ)。
- **P4 機構(use−observe、各セル対応差、符号検定)**: 提案を選ぶ割合 58.7%→38.9%(−19.8pt、42セルすべて減)、応答件数 676→448/セル(−228、42/42減)、拒否の割合 62.2%→17.1%(−45.1pt、42/42減)、従う割合 6.9%→11.2%(42/42増)、助言どおりの行動の割合 30.1%→18.1%(42/42減)。予測の方向(提案・応答・拒否の減少)はすべて当たった。助言どおりの行動の割合が下がるのは、拒否した選手の自分の選択が提案方法と偶然一致する分(拒否が多い基準の方がこの偶然一致が多い)が減るため、と読むが分解していない。`uptake<1`の提案候補は39.0%、平均`uptake`は0.892。
- **P5 結果(use−observe、168日、符号検定、有意水準0.0167)**: 平均真の技能 **−0.0017**(正4/負38、p<0.0001、有意)、平均trust **−0.0090**(正4/負38、p<0.0001、有意)、平均満足度は42セルとも差0。84日時点も同じ向き(技能−0.0008・p=0.044は検定外、trust−0.0070)。差は小さい(平均技能0.724に対し0.0017)が一貫した悪化。**原因は分解していない**: 受容度の高い選手へ提案を集中した結果、助言の総量(信頼を積む機会)が減って技能・信頼が下がった可能性はあるが検証していない。
- 記述: 完了移籍は平均 base/observe 1.79・use 1.71、イベント数は base 9,328・observe 10,004(信念イベント分)・use 9,590。useの信念平均の全体平均は0.429、0.2未満は3.4%。**探索の停止は起きなかった**: useで信念<0.2の15組は全て直近28日にも助言されていた(observeでは192組中172組)。`stated_reason`の累計は各信念の`reasons`に保存(判断には使わない)。

## 実装中の確認事項(規則・閾値は不変)

- 初回の実行は分析コードが`s_now`一定のセルのSpearman(None)を中央値に入れて停止した(全ゲートは通過済み)。Noneを除く修正をして全体を再実行した。式・閾値・検定は変えていない。
- ゲート6の比較で、番号の桁が変わると`obs_refs`と、それを写した`cause_refs`(`coach_training_decision`、`transfer_offer_requested`など)の文字列順が変わることが分かり、比較側で再ソートする規則を足した。実装の振る舞いの差ではない。
- ゲート3の「約10³本」は、応答3種×一定理由3種の長さ≤6(3,276本)と、全9記号の長さ≤3(819本)に具体化した(登録時の文言の曖昧さを、より広い方へ解釈)。

## 限界

- 更新式・減衰0.97・事前(1,1)・信号値・`uptake`の式は未校正。受容度は最も信頼する監督の1件の助言への応答の移動平均で、性格の推定ではない(特性は見ていない)。信念は監督ごとで共有されない。表明理由は回数の記録のみ。
- `uptake`は提案候補の価値だけを動かし、提案方法の変更や探索ボーナスは入れていない。結果(技能・trust)が下がった理由は未分解。3seedはセル間で独立でなく、符号検定は参考値。
- 信頼readerは、信念と矛盾しない形のイベント内容の改ざんをmanifest偽造後に受理し得る(T022の既知の限界)。認証は全再演readerのみ。

成果物: `artifacts/t029/{preflight,run}/`、`artifacts/t029/run-log.txt`、`artifacts/t029/tests-all.txt`、凍結ソース`artifacts/t029/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t029.py --out artifacts/t029/run`(新規ディレクトリのみ、約5分)。
