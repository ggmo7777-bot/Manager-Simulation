# T022 結果 — リーグ保存の読込高速化(信頼manifest付き直接reader)

実装・実験: 2026-11-10。契約: [T022](../tasks/T022.md)、事前条件: [PROTOCOL](T022-PROTOCOL.md)、判断: [D037](../DECISIONS.md)。目標・条件は実装前に固定し、結果後に変えていない。Python 3.13.3(uv)、macOS arm64。

## 結論

- `load_league_snapshot_trusted`を追加。保存形式・save版17・指紋scopeは不変で、既定の`load_league_snapshot`(全再演)は変更なし。成功条件1〜5をすべて満たした。
- 速度(同一プロセスで全再演と交互に3回ずつ、中央値): 1000日で fixed 5.80倍、sampled 4.14倍(目標3倍)、84日で 4.38倍 / 3.62倍(目標2倍)、35日で 4.32倍 / 4.26倍(報告のみ)。
- 高速化は**読込のみ**。保存(検証)、fork、resume後の保存は従来どおり全再演で、CLIは変更していない。
- 信頼readerは署名ではなく完全性検査。manifestも偽造・再計算された改変のうち、現金・型・余計なキーは検出するが、日数・疲労値の改変・末尾イベント削除は検出できない(下記)。

## 実装

`league_economy.py`に`load_league_snapshot_trusted`と共有の`_read_snapshot_json`を追加。手順: 重複キー拒否・非有限数拒否つきでJSON解析、同じ場所のmanifest.jsonの`snapshot_sha256`が`digest(raw)`と一致、snapshotとmanifestのidentity(save17とsource hash)が現行と一致、既存の`from_dict`で直接復元、`_validate_league_invariants`、イベントを除く全フィールドの再直列化が元と型まで完全一致(`_assert_same`)。イベントはファイルのリストをそのまま使い再照合しない(hashで束縛)。`league_economy.py`が指紋対象のためsource hashは変わった(`01711d1d…c229`、T021は`08606a0e…`)。T021以前のsaveは旧ソースで読む。

## 検査

| 条件 | 結果 |
|---|---|
| 1 同一性 | 7条件×fixed/sampled=14世界(seed100、35日)で、信頼読込の世界が全再演読込および元の世界と`_assert_same`で完全一致。再保存したsnapshot/events/summaryは両者と元でバイト一致 |
| 2 継続 | sampled open_solidarityの14日から信頼読込→21日進行が全再演読込と完全一致。14日で2件の記録介入(player_trust、coach_skill_estimate)の分岐を信頼読込すると`interventions`が保持され、3件目の介入と7日進行でも全再演読込と一致 |
| 3 負例(manifest整合のまま) | 値改変、float→int、余計なキー、現金改変、末尾イベント削除、日数改変、manifest欠落、manifest hash不一致、identity不一致、save16 identity、重複JSONキーの11種すべて拒否 |
| 4 速度 | 下表。目標達成 |
| 5 検査 | 全234テスト合格(111.5秒、3.13.3)、既定14条件のsnapshotがT018 rerun-py313と一致(identity/hash除く)、null=baseline 6組、旧成果物3,414ファイルhash不変 |

### 速度(秒、seed100、open_solidarity)

| 世界 | 日数 | 全再演(3回) | 信頼(3回) | 中央値の比 |
|---|---|---|---|---|
| fixed | 35 | 0.196/0.196/0.195 | 0.046/0.045/0.045 | 4.32 |
| fixed | 84 | 0.464/0.461/0.482 | 0.115/0.106/0.103 | 4.38 |
| fixed | 1000 | 7.826/6.579/7.528 | 1.246/1.297/2.039 | 5.80 |
| sampled | 35 | 0.212/0.206/0.213 | 0.050/0.061/0.049 | 4.26 |
| sampled | 84 | 0.488/0.493/0.491 | 0.116/0.136/0.136 | 3.62 |
| sampled | 1000 | 6.423/7.036/6.383 | 2.056/1.541/1.553 | 4.14 |

実装前の基準測定(1.2/2.5/14.6秒)は同じ全再演を測ったものだが、本実験の同条件の値(0.20/0.46/7.5秒)より大きい。実装前測定は他処理と並走して遅かった可能性が高く(PROTOCOLに注記済み)、絶対秒は実行間で比較できない。判定は同一プロセス内の比で行った。1000日で信頼readerの約1.3〜2秒の内訳(JSON解析・digest等)は計測していない。速度比は単一マシン・単一seedで、1000日でも約4〜6倍に留まる(日数比例ではなくなるが、解析とdigestの線形費用は残る)。

### 特性評価: manifestも偽造した改変(合否に使わない)

| 改変 | 全再演reader | 信頼reader |
|---|---|---|
| 現金 +1 | 拒否 | 拒否(会計保存則) |
| float→int | 拒否 | 拒否(再直列化の型) |
| 余計なキー | 拒否 | 拒否 |
| 疲労値の改変 | 拒否 | **受理** |
| 末尾イベント削除 | 拒否 | **受理** |
| 日数 +1 | 拒否 | **受理** |

信頼readerは、snapshotとmanifestを両方書き換えられる相手に対して規則違反の履歴を見逃す。認証が必要なら既定の`load_league_snapshot`を使う。

## 限界

- 単一マシン・単一seed、速度は約4〜6倍。保存・fork・resume保存は未高速化(これらは全再演検証が保存の契約)。
- manifestが現存し、かつsnapshotが保存時の検証を通ったことの証拠にのみ依存。外部データ・署名は対象外。
- CLIは未変更。信頼readerを使うresumeは、保存時に全再演を行うため速くならない。

成果物: `artifacts/t022/{baseline,run,review,preflight}/`、`artifacts/t022/run-log.txt`、凍結ソース`artifacts/t022/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t022.py --out artifacts/t022/run`(新規ディレクトリのみ)。
