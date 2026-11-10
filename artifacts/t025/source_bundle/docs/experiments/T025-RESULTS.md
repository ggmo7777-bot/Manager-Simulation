# T025 結果 — 加齢(年度末の記録付きイベント)

実装・実験: 2026-11-10。契約: [T025](../tasks/T025.md)、事前条件: [PROTOCOL](T025-PROTOCOL.md)(補正A1を含む)、判断: [D040](../DECISIONS.md)。規則と条件は実装前にコミットし、補正A1以外は結果後に変えていない。Python 3.13.3(uv)、macOS arm64。

## 結論

- `LeagueEconomyConfig.aging_start_age`(0=無効が既定、>0は`season_length_days`必須)とworldの`player_ages`を追加した。`advance_league_days`が`season_length_days`の倍数の日に全選手の年齢を+1し、イベント`season_aged`(`{season, ages}`)を記録する。効果は年齢カウンタの増加のみ。成功条件1〜7を満たした(条件2は補正A1の写像で)。
- `PlayerTruth`は無変更。有効時のみconfigに`aging_start_age`、snapshotに`player_ages`が増える。save版17と指紋scope(12ファイル)は不変で、`league_economy.py`が対象のためsource hashは変わる(T024以前のsaveは旧ソースで読む)。

## 検査

| 条件 | 結果 |
|---|---|
| 1 既定不変 | 既定14条件の126日digest(identity除く)がT023基準と一致、35日がT018と一致。既定のsnapshotに`player_ages`、configに`aging_start_age`、`season_aged`イベントはない |
| 2 効果は年齢のみ | 42セルすべてで、加齢ありworldから`season_aged`を除きIDを詰め直し、`aging_start_age`・`player_ages`を除くと、同じLの加齢なしworldのsnapshot全体(identity含む)と完全一致(補正A1の写像を使用) |
| 3 加齢の内容 | 42セルで`season_aged`が126//L件(L=21:6、42:3、84:1。計140)、日がLの倍数、agesが18+season。手計算(L=42、日42/84/126に19/20/21歳)に一致。最終年齢は18+126//L |
| 4 再演と保存 | 42セルを保存し、全再演・信頼の両readerで読んだworldが`_assert_same`で一致(`player_ages`含む)。介入1件を挟んだ分岐world(30日→fork→+60日、90日で2回加齢)も全再演readerで往復一致 |
| 5 改ざん検出 | `player_ages`の値変更、キー欠落、キー全削除、`season_aged`削除、イベントのages変更の5種×2reader=10件がすべて拒否。manifestまで偽造して信頼readerに通した特性評価も、不変条件(年齢=開始年齢+加齢イベント数、イベント内容の再検査)で拒否された(日数・疲労値の改変を見逃した T022の限界とは異なり、年齢は不変条件で検査できる) |
| 6 不正値 | 負・bool・float・strの`aging_start_age`、および`season_length_days=0`との組合せの5件がValueError |
| 7 検査 | 全unittest 246件合格(`artifacts/t025/review/tests-all.txt`は修正前の1件失敗を残す。`test_t024`のsource hash一致の検査は、T025が指紋対象のソースを変えたため当然に失敗し、T024の凍結bundleとT023の凍結bundleの比較(差は`standings.py`のみ)へ直した。`tests-all-rerun.txt`は直後の全件合格)、旧成果物3,979ファイルのhash不変、source_bundle保存 |

## 発見(補正A1)

予想(D040)は「IDを詰め直せば一致」だったが、一部反証された。`_record_ledger_tx`が原因参照に`world.events[-1]`を使うため、`season_aged`の直後に起きる最初の台帳イベントの`cause_refs`が`season_aged`のIDを指す。履歴の内容(日・金額・状態)は変わらず、原因参照の指し先だけが加齢イベントになる。42セルで`season_aged`を指すcause_refsは112件(L=21:各セル5、L=42:各2、L=84:各1。各セルの最終日の加齢には後続イベントなし、残る28件)。補正A1は`season_aged`のIDを直前の非加齢イベントのIDへ写すもので、結果の判定前に固定した。これは意味的には「加齢が台帳取引の原因」という誤った因果の記録になるので、将来`events[-1]`依存を解消するか、加齢イベントを因果鎖から外すかを別taskで判断する。

## 限界

- 年齢は何にも作用しない(技能・成長・移籍・給与・Policy)。成熟曲線・衰え・引退・新人は未実装。
- 全員18歳開始で年齢分布を持たない。年度長・開始年齢は任意の値。
- イベントIDは有効時に加齢イベント以降ずれる(仕様)。`events[-1]`による原因参照の誤誘導が1件/年度ある。
- 最初の実行(`run-log-attempt1-failed.txt`)は検査側の介入kind名の誤り、2回目(`run-log-attempt2-failed.txt`)は補正A1の原因となったcause_refs写像の`KeyError`、3回目(`run-log-attempt3-failed.txt`)は全42セルの検査が通った後の結果JSON化(set)の誤りで停止した。いずれも規則の変更なし(2回目のみ補正A1)。

成果物: `artifacts/t025/{preflight,run,review}/`、`artifacts/t025/run-log*.txt`、凍結ソース`artifacts/t025/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t025.py --out artifacts/t025/run`(新規ディレクトリのみ、約6分)。
