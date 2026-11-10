# T010 レビュー（敵対的監査・完了条件照合・限界と次段階への示唆）

記録日: 2026-11-07
監査・作成者: **Antigravity（Gemini 4 Argon）** — T001〜T007の作成者（`Sol 6.1 High` および当時の主担当）とは**別の作成者**としてT010を監査・記録する。

## 1. 完了条件（Acceptance Criteria）照合

[docs/tasks/T010.md](../tasks/T010.md) および [docs/GOVERNANCE.md](../GOVERNANCE.md) の完了条件に対する照合結果は以下の通り。

| 項目 | 要件 | 検証結果 | 根拠ファイル |
| :--- | :--- | :---: | :--- |
| 1. 作成者明記 | T001〜T007の作成者（`Sol 6.1 High`）とは別主体であることをコード・成果物・文書に明記 | **合格** | `TRANSFER_AUTHOR = "Antigravity (Gemini 4 Argon)"`, `summary.json`, `verification.json` |
| 2. 情報境界（A/B/Policy分離） | 監督・選手のどのPolicy関数（練習・先発選考・移籍交渉・2D試合）も世界の真値（`MatchTraits`, `rates`, `cap`, 他者の私的状態）を参照しない | **合格** | `tests/test_t010.py::test_non_leakage_in_coach_player_lineup_and_transfer_policies` |
| 3. 3者合意移籍と人数不変条件 | 固定イベント移籍を作らず、選手・放出側監督・獲得側監督の私的合意かつ `2 <= roster <= 4` のときのみ原子的に所属変更が成立する | **合格** | `tests/test_t010.py::test_three_party_mutual_agreement_required_for_transfer` |
| 4. 1パス保存・再開 | 再演なしに1パスで完全復元し、84日連続実行と42+42日再開、1,000+14日再開（1パス読込 `0.376s`）が完全バイト一致 | **合格** | `tests/test_t010.py::test_checkpoint_save_resume_exact_equality_all_conditions`, `summary.json` |
| 5. 破損検知（Fail-fast） | 12種類の改ざん・人数不変条件違反・不正移籍・原因参照不正snapshotを確実に拒否 | **合格** | `tests/test_t010.py::test_corrupted_snapshots_rejected` |
| 6. $2 \times 2$ 主比較と単一要因対照 | 育成結合×移籍窓口の主比較（240 runs）と3者主観の単一要因対照（90 runs）でキャリア・クラブ間成績の因果分離を実証 | **合格** | [docs/experiments/T010-RESULTS.md](T010-RESULTS.md), [artifacts/t010/experiments/summary.json](../../artifacts/t010/experiments/summary.json) |
| 7. 独立1パス意思決定・移籍監査 | Policy関数を一切呼ばず（`policy_functions_called == 0`）3,780決定＋748フィードバック＝4,528件を独立再計算し不一致 `0` | **合格** | [artifacts/t010/review/transfer-audit.json](../../artifacts/t010/review/transfer-audit.json) |
| 8. 過去資産保全 | T001〜T009の凍結485ファイル無改変、T009の240 runs意味射影一致、全109単体テスト合格 | **合格** | [artifacts/t010/review/verification.json](../../artifacts/t010/review/verification.json), [artifacts/t010/review/tests.json](../../artifacts/t010/review/tests.json) |

## 2. 開発中に発見・修正した設計上の教訓（自己監査記録）

1. **T009意味射影検査における識別子スキーマの差異**:
   - T007/T008のsnapshotは最上位の `versions["source_sha256"]` にソースハッシュを持つ一方、T009（`selection_loop`）のsnapshotは `identity["source_sha256"]` に格納していた。`artifacts/t010/preflight/semantic_projection.py` で両方のスキーマに対応したことで、T009全240 runsの意味論的完全一致（`identity.source_sha256` 以外の全状態・全履歴一致）を漏れなく検証できた。
2. **2クラブ直接2D試合におけるクラブ別統計抽出と選手IDマッピング**:
   - T009では `home` クラブのみが育成対象で `away` は固定基準選手（`a1, a2`）であったが、T010では両クラブ（`club_a`, `club_b`）の先発2名ずつ計4名がすべて `p1..p6` から動的に選出され、しかも移籍後は `p3` が `away`（`club_b`）側として出場する。
   - そのため、2D試合エンジン（`match_2d.py`）の `team='home'` / `'away'` と選手ID `p1..p6` の対応を試合時点の所属ロスターから厳密に引き直し、ボール保持tick・パス成功・シュート・タックル成功を両クラブ対称に集計するよう `_run_head_to_head_match` と `_extract_club_match_stats` を設計した。
3. **同一条件内での「残留定着キャリア」と「移籍開花キャリア」の自律分岐**:
   - 移籍をスクリプト化せず3者の私的評価の合意としたことで、`coupled_transfer` 条件のなかでも、`seed=17` のように前半戦の練習と出場で現所属監督 `c1` の評価を覆して主力第2枠に昇格した場合には `c1` が放出を拒否（`release_consented = False`）して `club_a` に残留する一方、前半戦でベンチが続いた多くのシード（Confirmation `28/30`）では Day 28 に `club_b` へ移籍して先発定着・後半戦の逆転劇を生むという、両方とも理由を追跡できるキャリア多様性が確認された。

## 3. 現段階の限界と次段階（M5以降）への課題

1. **契約条件・金銭台帳・育成補償が未導入**:
   - T010では「評価・出場機会・クラブ愛着の3者合意」による所属変更の因果を純粋に分離するため、移籍金・年俸・クラブ予算台帳（`Finance`）は導入していない。次段階（M5: 小規模世界）では有限の資金台帳と育成報酬を導入し、資金保存則と戦力補強・育成投資のトレードオフを検証する必要がある。
2. **2クラブ・6選手の最小閉ループ**:
   - 現在は2クラブ（`club_a`, `club_b`）・6選手（`p1..p6`）の直接対決であり、移籍先候補は常に相手クラブ1つのみである。M5では4〜8クラブの総当たり日程と複数クラブからの競合オファーへ拡張できる。
3. **試合中の途中交代・ポジション適性・世代交代**:
   - 先発2枠の試合単位固定であり、試合中の疲労交代やポジション別役割、および長期（M6）の加齢・引退・新人供給はまだ含まない。
