# T024 事前実験条件

2026-11-10、実装前登録。契約: [T024](../tasks/T024.md)。結果を見て条件・規則・seedを変えない。

## 固定する条件

seed100、Python 3.13.3(uv)。7条件(`T011_ALL_CONDITIONS`)×fixed/sampled=14世界、年度長 L ∈ {21,42,84}、126日(T023と同じ)。42セル=14世界×3長さ。年度長は`dataclasses.replace(config, season_length_days=L)`。match_interval_days=7(既定)なので126日で18試合日×2試合=36試合。

## 定義(`league_standings(world)`)

- 入力: `world.events`、`world.config.season_length_days`(>0必須、0はValueError)、`world.day`。
- 数える試合: `kind == "league_match_completed"`のイベントを1件1試合。他のkind(coach_match_observation等)は数えない。`data.home_club/away_club/home_goals/away_goals`のみ使用。
- 年度: イベントの`day`に対する`season_progress`の`season`。表を持つ年度は1..現在の年度(`season_progress(world)['season']`)。試合のない年度も4クラブ全て0行で出す。年度の`complete`は`world.day >= 年度番号×L`。
- 行: club、played、wins、draws、losses、goals_for、goals_against、goal_difference、points(勝3・分1・敗0)、rank(1始まり)、tied_with_previous。
- 順位: points降順 → goal_difference降順 → goals_for降順 → wins降順 → club_id昇順。直前の行と上の4キーが全て等しい行は`tied_with_previous=true`(club_id昇順で便宜的に決着)。
- 戻り値: `{"seasons": {年度番号(int): {"complete": bool, "table": [行...]}}}`。worldは変更しない。
- 不正: 年度長0はValueError。クラブ不明・得点が非負整数でないイベントはValueError。

## 成功条件

1. 既存不変: `soccer_sim/`の既存ファイルがT023とバイト一致(新規は`standings.py`のみ)、runtime source hashがT023のsource_bundleのidentity(`b6eb7dac…06e6f9`)と一致、既定14条件の126日snapshot digest(identity除く)がT023の基準と一致、全再演/信頼readerの保存往復が従来どおり。
2. 派生のみ: 関数呼出しの前後でworldの`_snapshot_payload_unchecked`のdigestが同一。42セルすべてで、`league_standings`は`world.events`のみから計算され、`club_accounts`を参照しない(コード上の参照なしと、イベントを別world由来に差し替えても結果がイベントに従うこと)。
3. 手計算: 小さな合成ケース(2年度、4クラブ、手書きイベント。勝点同点→得失点差、得失点差同点→総得点、全同点→club_id、非試合イベント混在、試合なし年度を含む)の期待表を実装前に手で作ってテストに書き、一致する。
4. 会計との整合: 42セルそれぞれで、全年度の合算がworld.club_accountsのpoints/matches_played/wins/draws/losses/goals_for/goals_againstと一致し、試合数が18試合日×2=36、各年度内で全クラブのplayedの総和=2×その年度の試合数、年度の試合数が`day`から数えた期待値と一致。
5. 独立再実装: `experiments/t024.py`内の第二実装(別の書き方: 保存済みsnapshot JSONの生dictだけを走査、年度は独自の算術、順位は別の集計方式)が、42セル全てで本実装と完全一致(表全体、行・順序・フラグ含む)。
6. 再計算の同一性: 42セルすべてを`save_league_artifacts`し、全再演reader・信頼readerの両方で読んだworldの`league_standings`が元worldと完全一致。
7. 不正値: 年度長0、未知クラブのイベント、負/bool/floatの得点が全てValueError。
8. 検査: 全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t024/preflight/historical-inventory.json`、3,785ファイル)のhash不変、source_bundle保存。

## 失敗条件

1〜8のいずれかの不一致、既存ファイルの変更、凍結成果物の変更、結果後の規則変更。失敗は失敗として記録する。

## 限界

順位表は分析/表示用の派生物で、昇降格・優勝・直接対決は未実装。club_id昇順の決着は便宜。4クラブ・総当たり1周=3試合日(各2試合)の小さな世界で、順位の面白さ(接戦の頻度など)は測らない。`standings.py`は指紋scope外なので、将来これをsnapshotや進行に使うときは指紋scopeを変える別taskで扱う。
