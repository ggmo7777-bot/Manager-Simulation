# 現在の状態 — 2026-11-10

**T030で世代交代の最初の最小単位(config`generations`、既定"none"。年度末に年齢と出場満足度だけで引退、同日に3人未満のクラブへ18歳の新人、理由つきイベント、既定は不変)を追加。T029で監督の受容度の信念(`coach_belief`、既定"none"。有効時は監督が`coach_advice_outcome`だけから選手ごとの受容度を推定してB層に持ち、`use_v1`のみ提案候補の価値に反映。真の特性は見えない)を追加。T028で助言への態度(config`advice_response`、既定"none"。助言を受けた日に選手が本人の状態と特性から 従う/部分従順/拒否 を決め、理由を記録。監督は結果だけ見る、既定は不変)を追加。T027で性格の潜在特性(config`personality`、既定"none"、2特性`discipline`・`emotional_stability`を選手の真の状態としてだけ保持。どの判断にも未使用でPolicy非公開、既定は不変)を追加。T026で年齢の効果(年度末の記録付きイベント`season_skill_aged`、未校正の仮説曲線、config`aging_curve`、既定は不変)を追加。T025で加齢(年度末の記録付きイベント`season_aged`、年齢カウンタのみ、既定は不変)を追加。T024で年度ごとの順位表(`league_standings`、イベントだけから導く純関数、保存しない)を追加。T023でM6の最初の単位として年度区切り(`season_length_days`、既定0=無効)を追加(進行・履歴は不変、加齢等はまだ無し)。T022でリーグ保存の信頼manifest付き直接readerを追加(読込のみ約4〜6倍)。T021でT020の拒否3摂動を再実行し、初期分布の範囲の感度掃引(単独摂動、範囲は不変)を19セルで完了。T019の初期分布の効果分解(2^3)を前提とする。** T018のリーグ実行指紋とCLI・分析来歴の分離を前提とする。 リーグsave17 / rules T015 / evaluation T016。4クラブ・4監督・12選手、A/B学習、日予算、2対2、選考、合意移籍、整数会計・育成還元、記録付き認識介入を持つ。最上位目的は独立個体の経験と選択から再現可能な歴史が生まれること。

## 最新の結果

- T030: `LeagueEconomyConfig.generations`(`"none"`既定、`"retire_replace_v1"`、`aging_start_age>0`必須)を追加。年度末の加齢後、`decide_retirement(age, 出場満足度)`(36歳以上は`age_limit`、33〜35歳で満足度0.5未満は`veteran_low_playing_time`、乱数・id・技能・特性は不使用)で引退し、同日にロスター3人未満のクラブへ18歳の新人(id p13〜、別乱数チャネル、T027の特性、監督の第一印象は定数)を受け入れる。引退者は記録に残りロスターと年齢表から外れる。イベント`season_retired`・`season_youth_joined`は理由つきで真の特性を含まない。不変条件が年齢・ロスター・新人をイベントから再導出して照合。主格子168+長期42セルで全10ゲート合格、全クラブ存続、リークなし。P1(年齢構造が動く)・P2(長期の技能水準)は支持、P3(新人は1年以内に90%以上観察される)は反証(60.9%、38.9%は最初の年度に先発なし。控え若手を見つける仕組みが無い)。補正A1: 移籍で人数が3未満になると引退なしでも新人が入る。式・数値は未校正。全272テスト合格。[T030結果](experiments/T030-RESULTS.md)。
- T029: `LeagueEconomyConfig.coach_belief`(`"none"`既定、`"observe_v1"`、`"use_v1"`、`advice_response`必須)を追加。監督は`coach_advice_outcome`(態度・3値の表明理由)だけから、監督×選手の受容度の信念(Beta疑似回数、事前(1,1)、減衰0.97、信号 従う1/部分0.5/拒否0)をB層で更新し、イベント`coach_belief_update`に記録(信念は結果イベントの畳み込みで、不変条件が再計算して照合)。`use_v1`のみ提案候補の`need_term`・`forecast_term`へ`uptake=min(1,mean/0.5)`を掛ける(`observe_v1`は記録のみで判断不変)。Cの学習なし。既定とT028のon42セルは完全一致、observeはbaseと(信念イベントを除き)一致、信念は特性・状態に触れない(静的/入力/動的)。信念は真の現在の受容度とSpearman中央値+0.83、定数より近い42/42、特性との相関は弱い(+0.18/−0.17)。useは提案割合58.7→38.9%・拒否62.2→17.1%と機構は予測どおりだが、技能−0.0017・trust−0.0090と有意な小さい悪化(原因未分解)、満足度は無差で、受容度の利用が成長に効くとは言えない。改ざん3種は信頼readerも拒否。全265テスト合格。式・係数は未校正。[T029結果](experiments/T029-RESULTS.md)。
- T028: `LeagueEconomyConfig.advice_response`(`"none"`既定、`"personality_v1"`、`personality`必須)を追加。助言を受けた日の練習判断の直前に、`score=(trust−autonomy)+0.25(discipline−0.5)−0.40(1−emotional_stability)·dissatisfaction`から 従う(≥0)/部分従順(≥−0.20)/拒否 を決める(純関数、乱数なし)。部分は従来の加点判断、拒否は助言なし判断、従うは提案方法。本人の理由はイベント`player_advice_response`(項・理由コード)、監督が見るのは`coach_advice_outcome`(態度・3値の表明理由・従ったか)のみで真の特性は見えない(静的/動的/イベント検査)。監督はまだ使わない。既定14条件はT018・T023基準と一致、42セル×(off/mid/on)で全28,402応答を独立再計算と一致。応答は従う6.7%・部分30.8%・拒否62.6%(主因は信頼<自律で性格より信頼の状態)。規律性が高いほど従い(5.6→8.7%)、不満時は感情安定性が低いほど拒否(92.5→72.0%)。機構の効果(mid−off)は技能−0.0030・信頼−0.0059、特性の効果(on−mid)は技能+0.0024で有意だが小さく理由は未分解、満足度は無差、特性と成長の相関は見えない。信頼readerは応答改ざんをmanifest偽造後に受理(T022の限界)。全260テスト合格。係数・閾値は未校正。[T028結果](experiments/T028-RESULTS.md)。
- T027: `LeagueEconomyConfig.personality`(`"none"`既定、`"latent_v1"`)とworldの`player_personality`を追加。有効時のみ全選手に`discipline`・`emotional_stability`(0.15〜0.85一様、別チャネル`personality:<名>`のキー付きdraw)を持たせ、判断・成長・観測・イベントでは読まない。野心・自律(従順の逆)・忠誠は`PlayerMind`に既にあるので複製せず、全選手同一の直書き定数(満足度反応幅、休養選好)に対応する2特性を足した。既定14条件はT018・T023基準と一致、14世界×3seedと(季・加齢併用)14世界で有効化しても履歴が完全一致、特性を極端値に差し替えても履歴が不変、静的検査で参照は許可リスト8関数のみ。24,000値の分布は宣言どおり(KS 0.0065/0.0100、相関|r|≤0.013)。保存・再演・分岐が一致、改ざん21件(manifest偽造後の信頼readerも不変条件で)拒否、全255テスト合格。個体差の行動への効果は未検証(T028)。[T027結果](experiments/T027-RESULTS.md)。
- T026: `LeagueEconomyConfig.aging_curve`(`"none"`既定、`"hypothesis_v1"`、`aging_start_age>0`必須)を追加。年度末の`_age_players`が`season_aged`直後にイベント`season_skill_aged`を記録し、全選手の3特性へ到達年齢のdelta(+0.02(<=24)、0(25..29)、-0.01*(a-29)(>=30)、[0.15,cap])を一度だけ加える。数値は未校正の仮説。技能を変えるのはこの1か所で、決定関数・Policy・観測は年齢を読まない(AST検査)。既定14条件はT018・T023基準と一致、84セル(14世界×年度長{21,42,84}×開始年齢{18,29})で規則一致・再演/信頼reader往復一致・改ざん4種拒否、全250テスト合格。最終日の平均技能差は開始18で+0.016〜+0.082、開始29で-0.008〜-0.177(名目の約68〜87%、残りは訓練が相殺)、完了移籍件数は変わらない。[T026結果](experiments/T026-RESULTS.md)。
- T025: `LeagueEconomyConfig.aging_start_age`(0=無効、>0は`season_length_days`必須)とworldの`player_ages`を追加。`advance_league_days`が年度末の日に全選手の年齢を+1し、イベント`season_aged`(season、各選手の年齢)を記録する。`PlayerTruth`は不変で、有効時のみconfigとsnapshotに任意キーが増える(save17・指紋scope不変)。全再演readerは進行が加齢を再現するので新しい再演経路なし。既定14条件はT018・T023基準digestと一致。14世界×年度長{21,42,84}=42セルで、`season_aged`を除きIDを詰め直すと加齢なしworldと完全一致、再演・保存・信頼readerの往復一致、改ざん10件拒否、不正値5件拒否、全246テスト合格。発見: `_record_ledger_tx`が原因参照に`events[-1]`を使うため、`season_aged`直後の台帳イベント1件のcause_refsが加齢イベントを指す(140件中112件、補正A1)。年齢は何にも作用せず、全員18歳開始。[T025結果](experiments/T025-RESULTS.md)。

- T024: `soccer_sim/standings.py`の`league_standings(world)`を追加。`league_match_completed`イベントだけから、T023の年度ごとにクラブの試合数・勝分敗・得失点・勝点・順位を導く(勝3分1敗0、順位は勝点→得失点差→総得点→勝数→club_id、最後は便宜で`tied_with_previous`に明示)。既存ファイルは無変更でsource hash・save17はT023と同じ(指紋scope外)。手計算の合成ケースに一致、14世界×年度長{21,42,84}=42セルで会計との合算・独立再実装・全再演/信頼の両readerからの再計算が一致、不正値7件拒否、全242テスト合格。年度長21ではclub_id決着の行が84年度行中16、42は3/42、84は0/28。昇降格・優勝・Policyへの公開・表示は未実装。[T024結果](experiments/T024-RESULTS.md)。
- T023: `LeagueEconomyConfig.season_length_days`(0=無効、有効時のみconfigにキー)、`season_progress(world)`(日から導く年度番号・年度内日数・完了年度数)、`advance_league_seasons(world, seasons, on_season_end)`(年度末ごとにhookを呼ぶ)を追加。加齢を先にしなかったのは、選手フィールド追加が既定snapshotを変えるため。14世界×年度長{21,42,84}=42セルの126日で、有効化しても`season_length_days`を除く履歴(events含む)が既定と完全一致。既定14条件はT018・実装前digestと一致、全再演/信頼の両readerで往復一致、不正値11件拒否、全238テスト合格。加齢・引退・新人・年度ごとの移籍窓・順位表・年度末イベントは未実装(hookは空)。[T023結果](experiments/T023-RESULTS.md)。
- T022: `load_league_snapshot_trusted`を追加(manifestのsnapshot_sha256とidentityに束縛し、from_dictで直接復元、再直列化で完全一致を確認)。保存形式・save17・指紋scopeは不変、既定readerは全再演のまま。14世界で全再演読込と完全一致、resume・多段分岐・介入保持が一致。1000日の読込はfixed 5.80倍/sampled 4.14倍、84日は4.38/3.62倍(同一プロセス内の比、目標3倍/2倍)。保存・fork・resume保存は全再演のままで速くならない。manifestも偽造した改変では日数・疲労値・末尾イベント削除を見逃す(署名ではない)。全234テスト合格。[T022結果](experiments/T022-RESULTS.md)。
- T021: T020で拒否された3摂動(cap_w15、psych_w15、cap_cp)を、結果前に固定した規則(cap天井0.98、幅摂動の対称切り詰め)で再実行。T020の16セルは1,440行で全一致。新3セルのΔは−0.000016/−0.000165/+0.000434(baseline +0.006729、検出可能差0.002308)で検出可能差以下。19セルでH1支持・H2支持(成長率群が最大)・H3は変わらず判定不能。psych_w15_xは忠誠が下端で拘束され広がらない。全230テスト合格。[T021結果](experiments/T021-RESULTS.md)。
- T020: 初期範囲を1つずつ動かした育成還元効果(初期育成6名、seed300..329、baseline +0.006729、検出可能差0.002308)。事前登録18摂動のうち3種(cap_w15、psych_w15、cap_cp)は同じ事前登録の値域規則で拒否され未実行(16/19セル、残り3セルはT021で実行)。実行15種は全て正(H1支持)。最も効くのは成長率の中心(rate_cp +0.004226、rate_cm −0.002811、H2は点推定で支持)、印象誤差幅の最大変化は0.001782(H3判定不能)。自己推定バイアス幅は影響なし。校正目標表は内部基準を検査済み、外部基準は実データ要(未決)。範囲は変更なし、既定14条件はT018と一致、全225テスト合格。[T020結果](experiments/T020-RESULTS.md)。
- T019: 育成還元の効果(初期育成6名、開放・還元あり−なし)の縮小(fixed→sampled)を3要因に分解。確認seed(200..229)で000 +0.016139→111 +0.008012。主効果はプロフィールF1 −0.004109、初期印象F2 −0.003551、満足度F3 −0.000084(ほぼ0)。F1とF2が同程度で、順序によりF1の割合は28〜66%。H2(F1最大)・H3は支持されず。既定14条件はT018とバイト一致、角セルはT016を再現、218テスト合格。[T019結果](experiments/T019-RESULTS.md)。
- snapshot identityの`source_sha256`を`league-runtime-files-v1`の12実行ファイル指紋にした。CLI入口・package全体・分析(`experiments/custom_growth/*`含む)はmanifestの来歴のみ。現在のruntime hash `0f71cbee…2842fad5`。
- 全211テスト合格（86.393秒、Python 3.14.0）、高速50件（2.7秒）。14条件×seed100×35日が凍結T017とCLI/APIで一致。非実行変更5種×5操作（再開・分岐・多段）はsnapshot/events/summaryがバイト一致。
- 実行ファイル12件の変更と負例7種（未登録import・動的import・不明scope・改変hash・旧save16等）を出力作成前に拒否。2,993取引を独立監査。旧2,147ファイル不変。
- cache追加後の再実行はPython 3.14.0だったが、基準3.13.3でも再確認(211テスト合格、14世界一致)。3.13.3と3.14.0でsnapshotが45/45バイト一致し、初回との差はcache追加(ソース変更)が原因でPython版ではない。
- T016の初期育成全員の比較と移籍なし集団の記述分離は維持。T016の180世界・初期育成6名の還元差（fixed +0.015301、sampled +0.007379）は過去の評価結果で、今回の多seed再分析はない。

[T018結果](experiments/T018-RESULTS.md) / [契約](tasks/T018.md) / [T017結果](experiments/T017-RESULTS.md) / [T016結果](experiments/T016-RESULTS.md)。

## 検証済みの基盤

T013は疲労測定/B更新、実請求・循環オファーの受付、主体ごとの移籍判断、介入の原因参照を修正。[結果](experiments/T013-RESULTS.md)。T014は整数通貨へ移行し、経済240run/分岐30系列/1000日で旧版と正規化後の全履歴が一致。[結果](experiments/T014-RESULTS.md)。T012以降は許可したB変数への研究介入を記録し分岐保存/再開できる。[結果](experiments/T012-RESULTS.md)。

T015は初期分布の範囲生成を追加。固定4名の旧「残留側成長」という解釈はT016で改訂した。旧数値はlegacy診断として保持。[旧結果と注意](experiments/T015-RESULTS.md)。

## 制約と次の小さな候補

1. 指紋はファイル単位で保守的。同居する旧writerや評価だけの変更でも再開を拒否する。静的import解析はこのpackageの契約で、汎用安全解析ではない。
2. 初期範囲は未校正(T019で効果を分解したが範囲は校正していない)。F2のうち台本とノイズの分離は未実施。T016のラベル付替え検査は評価器に限定し、世界全体の一般化とは呼ばない。
3. 既定の保存・読込は全履歴再演(1000日で約6〜8秒、T022再測)。信頼readerは読込のみ約4〜6倍で、manifestを偽造されると規則違反を見逃す。保存・fork・resume保存は未高速化。記録外介入は保存不可。
4. リーグsave17は12実行ファイルのhash。save16以前は自動移行せず、T015/T016/T017等の旧保存は対応する旧ソースで読む。評価版はsummary/manifestに記録。
5. 移籍窓は日28/56のみ(年度区切りはT023で追加したが、窓は年度ごとに繰り返さない)。引退・新人・毎季市場・C・3D/UIは未実装(加齢はT025で年齢カウンタ、T026で未校正の仮説曲線による技能の変化のみ。引退・新人・年齢のPolicy公開は未実装)(順位表はT024で派生集計のみ。表示・Policy公開・昇降格なし)。初期集団生成は世代交代ではない。
6. 世界間の重複、直書き係数、clamp/round6重複は残る。利息/税/分割配分の丸めも未定義。次候補の記載は一括着手指示ではない。

[外部14点の対応](reviews/2026-11-08-EXTERNAL-REVIEW.md)。

## 過去結果への索引

| 段階 | 主な範囲 / 結果 |
|---|---|
| T001–T003 | [基盤](experiments/T001-RESULTS.md) / [認識](experiments/T002-RESULTS.md) / [信頼](experiments/T003-RESULTS.md) |
| T004–T007 | [自己予測](experiments/T004-RESULTS.md) / [監督予測](experiments/T005-RESULTS.md) / [方法選択](experiments/T006-RESULTS.md) / [複数監督](experiments/T007-RESULTS.md) |
| 追加検証 | [旧STATUSの6改善表](experiments/T007-IMPROVEMENT-RESULTS.md) |
| T008–T010 | [2D競技](experiments/T008-RESULTS.md) / [出場機会](experiments/T009-RESULTS.md) / [合意移籍](experiments/T010-RESULTS.md) |
| T011/R1 | [元版](experiments/T011-RESULTS.md) / [成長/保存/選手境界の修正](experiments/T011-R1-RESULTS.md) |

過去の段階: T015〜T016は上記、[T017結果（sampled集団のCLI接続、save16）](experiments/T017-RESULTS.md)。

T011/R1の旧「100%拒否」は実再送検査がなく、ガード有効性の根拠として撤回。疲労真値が入る旧結果を十分な認識分離の証拠にしない。凍結artifactは保持し、新結果で上書きしない。

運用: README → CHARTER → 本書 → 対象task/結果 → GOVERNANCE/DECISIONS/SOL-HANDOFF。通常の境界確認は`python3 -B tools/check.py --suite fast`、段階完了は全unittest。新runは既存ディレクトリへ上書きしない。Git未導入のため、`tools/artifact_inventory.py`でキャッシュ等を除外して凍結/照合する。
