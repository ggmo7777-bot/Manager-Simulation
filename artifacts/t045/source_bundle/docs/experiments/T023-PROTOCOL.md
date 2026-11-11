# T023 事前実験条件

2026-11-10、実装前登録。契約: [T023](../tasks/T023.md)。結果を見て条件・長さ・seedを変えない。

## 固定する条件

seed100、Python 3.13.3(uv)。7条件(`T011_ALL_CONDITIONS`)×fixed/sampled=14世界。年度長 L ∈ {21, 42, 84}(21=ラウンドロビン1周、42=2周、84=12試合日)。年度長は`dataclasses.replace(config, season_length_days=L)`で与え、条件名の接尾辞は作らない。

年度の定義(L>0): 完了年度数 = `day // L`、年度番号 = `(day-1)//L + 1`(day0は1)、年度内日数 = `(day-1)%L + 1`(day0は0)。年度末 = dayがLの倍数になった日。

## 成功条件

1. 既定不変: 既定14条件(35日)のsnapshot/events/summaryが`artifacts/t018/rerun-py313`と(identity/snapshot_sha256除き)一致し、configに`season_length_days`キーがない。また実装前の既定14条件(126日)のsnapshot digest(identity除く)を、実装前に`artifacts/t023/baseline/`へ記録し、実装後の既定と一致させる。
2. 有効でも履歴同一: 14世界×L∈{21,42,84}の126日で、`_snapshot_payload_unchecked`から`config.season_length_days`を除いた全体(events全件を含む)が既定worldと完全一致し、config側は`season_length_days`が唯一の差。
3. 年度の読み取りとhook: sampled open_solidarity(L=42)で、(a) 0日から3年度進行はhookが日42,84,126で呼ばれ、渡された完了年度数が1,2,3で、結果のworldが`advance_league_days(126)`と`_assert_same`で一致、(b) 10日から2年度は日42,84で呼ばれ84日で停止、(c) 年度末(42日)から1年度は日84のみ、(d) 0年度は何もしない。読み取り関数が日0..130の全日で上の定義式と一致。
4. 保存: L=42の126日worldを`save_league_artifacts`し、`load_league_snapshot`(全再演)と`load_league_snapshot_trusted`の両方でconfig・状態が元と`_assert_same`、年度の読み取りも一致。既定worldの保存にはキーがない。
5. 不正値: `season_length_days`が負、bool、float、strのconfigは構築時にValueError。L=0のworldで年度読み取り・`advance_league_seasons`はValueError。年度数が負・bool・floatはValueError。hookが呼べないものはValueError。いずれもworldを変更しない。
6. 検査: 全unittest合格(3.13.3)、旧成果物inventoryのhash不変、source_bundleを保存。

## 失敗条件

1〜5のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の条件変更。失敗は失敗として記録する。

## 限界

年度は時間単位であって、加齢・引退・新人はまだ何もしない。移籍窓(日28/56)は最初の年度にしか来ない既存の制約が残る。年度長の値(21/42/84)は任意で、現実のサッカーの年間日程とは無関係。
