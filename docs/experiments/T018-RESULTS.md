# T018 結果 — リーグの実行指紋とCLI・分析の来歴の分離

2026-11-10。実装Sol 6.1 High、設計・独立検証・記録Codex。[事前契約](../tasks/T018.md)、[事前条件](T018-PROTOCOL.md)。

リーグのsnapshot identityの`source_sha256`を、`source_scope=league-runtime-files-v1`の12実行ファイルの指紋に変更した。CLI入口、package全体、分析コード(package内`experiment*.py`とroot`experiments/**/*.py`)はmanifestの来歴に別記し、snapshotや親checkpoint hashへ混ぜない。保存はリーグのみsave17。進行T015、Policy T013、評価T016、通貨、初期集団、A/B学習、全再演readerは維持。旧世界のstorage.identityは変更なし。

実行ファイル: `__init__.py`、`advice.py`、`fingerprints.py`、`league_economy.py`、`league_observation.py`、`match_2d.py`、`money.py`、`observation.py`、`policy.py`、`simulation.py`、`state.py`、`storage.py`。内部importは静的に検査し、未登録依存・欠落・動的importは拒否する。分析import例外は`storage.write_run -> experiments`と`match_2d.write_run -> experiment_t008`のみ(ファイル/関数/モジュールの組で限定)。

## 検証結果

| 検証 | 実測 |
|---|---|
| 全unittest | 211件合格、86.393秒(Python 3.14.0) |
| 高速ゲート | 50件合格、2.7秒 |
| 14世界(fixed/sampled各7条件、seed100、35日) | 凍結T017と14/14一致、CLIとAPIも14/14一致。除外はidentityと派生snapshot hashのみ |
| 非実行変更5種(CLIコメント、package分析コメント/追加、root分析コメント/追加) | 各変更で再開・記録付き分岐・分岐の再開・多段分岐・その再開の5操作がsnapshot/events/summaryバイト一致。親hash一致。manifestの来歴だけ変化 |
| 実行ファイル12件の変更 | 全件で再開を拒否(hashが変わる) |
| 負例7種 | 実ルール変更、未登録内部import、動的import、不明scope、改変hash、旧save、実T017 save16をすべて出力作成前に拒否。親・基底分岐は不変 |
| 独立監査 | 2,993取引で一致。167要求、10配当決済、claim受理10・重複3 |
| 旧成果物 | 2,147ファイルのinventory照合、不一致0(本記録時に再照合) |

実行時間(観測値、性能ベンチマークではない): 初回89.627秒(Python 3.13.3)。初版は指紋解析が10回で0.624秒、T017回帰6件が25.293秒(旧7.519秒)だったため、同一内容の構文検査だけをファイル名とsource bytesで最大48件再利用する`lru_cache`を`fingerprints.py`に追加した(存在確認・読出し・hashは毎回行う)。cache後の再実行は48.065秒。

### 再実行(cache後)と逸脱

cache後の再実行は`artifacts/t018/rerun-after-cache/`。**Python 3.14.0で実行した。** このマシンに3.13がなく、プロジェクト基準の3.13.3とは異なる。これは計画からの逸脱であり、全unittest(211件)も3.14.0で実行した。

初回(3.13.3)と再実行(3.14.0)で一致した項目: `frozen_t017_equal=14`、`cli_api_equal=14`、`baseline_chain_api_equal`、`parent_and_base_branches_unchanged`、監査取引2,993、admission_totals、負例、実行ファイル12件の拒否、`changed_package_files`。5種の変更×5操作もバイト一致。

差が出た項目: Pythonバージョン、runtime sha256、`fingerprints.py`のhash、分析ファイル一覧(`experiments/custom_growth/*`の6件が追加)、`snapshot_sha256`。identityの`source_sha256`が`027a191b…`から`0f71cbee…`へ変わったのはcache追加(ソース変更)による。ただしidentityの入力にsource_sha256が含まれるため、3.14.0の影響とソース変更の影響は分離できていない。3.13での再実行は未実施。

## 版と保管

save17、scope `league-runtime-files-v1`。現在のruntime source hash(再実行のmanifest): `0f71cbee760bd6e0b914679437057e93fbd5822af7906aa1c447f2ee5842fad5`。初回の値`027a191b…`の保存はcache追加前のソースに対応する。T017以前の保存(save16含む)は自動移行せず拒否し、対応する凍結ソースで読む。指紋は書き換えない。

証拠: [初回比較](../../artifacts/t018/comparison/summary.json)、[cache後の再実行](../../artifacts/t018/rerun-after-cache/summary.json)、[実行コマンド](../../artifacts/t018/rerun-after-cache/commands.json)、[全テスト](../../artifacts/t018/review/tests-all.txt)、[初版ソース](../../artifacts/t018/iteration1/source_bundle)、[旧成果物inventory](../../artifacts/t018/preflight/historical-inventory.json)。

## 残る制約

1. ファイル単位で保守的。同居する旧writer、評価、直接使わない部分のみの変更でも再開を拒否する。
2. 静的import解析はこのpackageの契約であり、汎用Python解析でも署名でも任意コードの安全性証明でもない。外部データ/モデル導入時は契約を再設計する。
3. Python 3.14.0での再実行は上記の逸脱。3.13.3基準の完全な再確認は未実施。
4. `experiments/custom_growth/*`が分析来歴に入った。実行指紋には影響しない。
5. 実行中のソース変更は未対応。保存フォルダ全体の原子性は保証しない。
6. 35日・seed100の比較であり、新しい頑健性・多seed・1000日の結論を出さない。生成分布の校正、世代交代、C、毎季市場、全再演readerの高速化は未実施。
