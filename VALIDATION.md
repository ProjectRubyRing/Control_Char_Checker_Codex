# 検証記録

検証環境はWindowsネイティブのWindows PowerShell 5.1、Python 3.12.10です。

| 確認 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 22件成功。PythonとPowerShellを実行して結果を比較 |
| `python -m compileall -q lib tools tests` | 成功 |
| Python 3.9の文法としてASTを解析 | 成功。3.9ランタイムでの実行を意味するものではない |
| PowerShell 5.1の構文解析、C#の`Add-Type`コンパイル | 成功 |
| Node.jsによるレポートの描画処理・詳細展開・全検出位置・検索・フィルター | 成功。DOMを模した環境での確認 |
| 新規ソース・定義19ファイルの空白・UTF-8・改行形式 | 成功。PS1のBOM・CRLF、SHのBOMなし・LFを確認 |

Unicodeの全検査範囲と23,188件のレガシー変換表を検証しています。CRLFの分断、多バイト文字の分断、先頭サンプルより後の検出文字、末尾の不正データ、UTF-16/32、ISO-2022-JP、読み取り不能、循環リンク、ファイルロック、上書き防止も対象です。

RHEL 9.8、Linux Bash、Python 3.9ランタイム、PowerShell 7での実行はこの環境では未検証です。WSLは使用できず、Dockerエンジンへのアクセスは拒否されました。Git Bashでの`bash -n`もシグナルパイプ作成時のアクセス拒否により実行できませんでした。シェルラッパーは目視レビューしています。ブラウザーのheadlessスクリーンショットもこの環境では生成できなかったため、実ブラウザーの表示確認は未実施です。

RHEL側での確認には次を使用してください。

```bash
bash -n ./check-text-files.sh
bash ./check-text-files.sh ./examples/input --output /var/tmp/text-check-example.html
# サンプルは要確認文字を含むため、正常な検査結果は終了コード1
python3 -m unittest discover -s tests -v
```

作業ディレクトリは初期状態からGitリポジトリではありません。通常の`git diff`は利用できないため、新規ソース・定義ファイルを空ファイルと比較する`git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --no-index --check`で空白エラーを確認しています。CRLFのCRを末尾の余計な空白と誤判定しない設定です。サンプル入力の改行・制御文字は意図的な検査データです。

最初のテスト実行では、Pythonが作成する所有者限定ACLの一時ディレクトリに、制限付きプロセスからアクセスできませんでした。テスト用ディレクトリは親のACLを継承する方式へ修正済みです。その際に作成された`checker-test-`で始まる空ディレクトリ18個は、実行環境の権限制限により削除できず、作業ディレクトリに残っています。配布ZIPには含めていません。
