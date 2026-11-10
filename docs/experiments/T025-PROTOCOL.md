# T025 事前実験条件

2026-11-10、実装前登録。契約: [T025](../tasks/T025.md)。結果を見て条件・規則・seedを変えない。

## 固定する条件

seed100、Python 3.13.3(uv)。7条件(`T011_ALL_CONDITIONS`)×fixed/sampled=14世界、年度長 L ∈ {21,42,84}、126日(T023/T024と同じ)。42セル。加齢ありは`replace(config, season_length_days=L, aging_start_age=18)`、加齢なし対照は`replace(config, season_length_days=L)`。

## 定義

- `aging_start_age`: 整数、既定0=無効。>0なら`season_length_days`>0が必須(違反は構築時ValueError)。負・bool・float・strはValueError。
- 有効時、worldに`player_ages`(全`players`のID→整数)。開始時は全員`aging_start_age`。`advance_league_days`でdayが`season_length_days`の倍数になった日、その日の通常処理の後に全員+1し、イベント`{"kind": "season_aged", "actor": "league", "day": その日, "data": {"season": day//L, "ages": {player_id昇順: 加齢後の年齢}}, "cause_refs": []}`を1件追加する。
- snapshot: 有効時のみトップレベルに`player_ages`、configに`aging_start_age`。既定にはどちらも出さない。
- 不変条件(`_validate_league_invariants`): 無効なら`player_ages`は空。有効なら`player_ages`のキー集合が`players`と一致、`season_aged`イベント数が`day//L`、各年齢が`aging_start_age`+そのイベント数、イベントの`ages`が各時点の年齢と一致。

## 成功条件

1. 既定不変: 既定14条件の126日snapshot digest(identity除く)がT023基準(`artifacts/t023/baseline/baseline.json`)と一致し、35日がT018(`t019.check_regression`)と一致。既定のsnapshotに`player_ages`、configに`aging_start_age`がない。
2. 効果は年齢のみ: 42セルで、加齢ありworldから`season_aged`イベントを除いて残りのイベントIDを詰め直し(`e<n>`形式の文字列を全箇所で写像)、configの`aging_start_age`と`player_ages`を除くと、同じLの加齢なしworldのsnapshot全体(identity含む)と完全一致。
3. 加齢の内容: 42セルで`season_aged`イベントが`126//L`件、日がLの倍数、season=1..、agesが18+season、全選手の最終年齢が18+126//L。手計算でL=42の期待(日42,84,126に19,20,21歳)と一致。
4. 再演と保存: 42セルを`save_league_artifacts`し、全再演reader・信頼readerの両方で読んだworldが元と`_assert_same`で一致(`player_ages`含む)。介入(信頼介入1件)を挟んだ分岐worldでも全再演readerで往復一致。
5. 改ざん検出: 保存済みsnapshotの`player_ages`の値変更、`season_aged`イベントの削除、イベントのagesの変更、`player_ages`のキー欠落が、全再演readerで拒否される。信頼reader(manifest整合の偽造なし)でもsnapshot変更は拒否される。manifestも偽造した場合の限界は特性評価として記録(合否外)。
6. 不正値: `aging_start_age`が負・bool・float・strのconfigはValueError。`aging_start_age>0`かつ`season_length_days=0`はValueError。
7. 検査: 全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t025/preflight/historical-inventory.json`、3,979ファイル)のhash不変、source_bundle保存。

## 失敗条件

1〜7のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の規則変更。特に条件2は、イベントIDの位置が挙動に影響する場合(例: ID文字列を使う箇所)に反証され得る。失敗は失敗として記録する。

## 限界

年齢は記録上のカウンタで、技能・移籍・給与・Policyには何も作用しない。全員同齢スタートで年齢分布を持たない。引退・新人・成熟曲線は未実装。年度長の値は任意で、18歳開始も任意。
