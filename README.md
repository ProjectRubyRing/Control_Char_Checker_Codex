# テキストファイル検査ツール

指定ディレクトリを再帰的に走査し、拡張子、文字コード、BOM、改行コード、制御文字・特殊空白・不可視文字を検査します。結果は**Meiryo UI指定のモノトーンHTML**と、機械処理用のJSONに出力します。入力ファイルを書き換える処理はありません。

表示例は[サンプルレポート](examples/demo-report.html)をブラウザーで開いて確認できます。検査用の入力を`examples/input`に同梱しています。

## 実行方法

`check-text-files.sh` / `check-text-files.ps1` と、`lib`、`data`、`assets` を同じ構成のまま配置してください。実行時のネットワーク接続や追加のPythonパッケージは不要です。

### RHEL 9.8向け

BashとPython 3.9以上を使用します。RHEL 9の標準Python 3.9に対応する実装です。[Red HatのPython環境の説明](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/installing_and_using_dynamic_programming_languages/index)

```bash
# python3がない環境では、管理者が事前にインストール
sudo dnf install python3

bash ./check-text-files.sh '/srv/project' --output '/var/tmp/check-report.html'

# 文字コードが確定している場合
bash ./check-text-files.sh '/srv/project' --encoding cp932 --output '/var/tmp/cp932-report.html'

# 既存レポートを置き換える場合
bash ./check-text-files.sh '/srv/project' --output '/var/tmp/check-report.html' --force

bash ./check-text-files.sh --help
```

シェルファイルはUTF-8・BOMなし・LFです。

### Windows 11向け

Windows PowerShell 5.1とPowerShell 7に対応する構成です。Pythonや追加モジュールは不要です。検査部分は同梱C#を`Add-Type`で読み込みます。日本語のレガシー文字コードには同梱の厳密な変換表を使用し、.NETのEUC-JP実装による不正バイトの受け入れやJIS X 0212の非対応を回避しています。

```powershell
.\check-text-files.ps1 -Path 'C:\work\対象ディレクトリ' -Output 'C:\reports\check-report.html'

.\check-text-files.ps1 -Path 'C:\work' -Encoding cp932 -Output 'C:\reports\cp932-report.html'

.\check-text-files.ps1 -Path 'C:\work' -Output 'C:\reports\check-report.html' -Force

Get-Help .\check-text-files.ps1 -Examples
```

実行ポリシーでローカルスクリプトが拒否される環境では、組織の運用ルールに従って実行してください。PS1はPowerShell 5.1で文字化けしないUTF-8 BOM付きです。

出力先を省略すると、現在の作業ディレクトリに`text-check-report.html`と`text-check-report.json`を作成します。既存の同名ファイルがある場合は、明示的な`--force` / `-Force`が必要です。HTMLとJSONはそれぞれ一時ファイルから公開します。2ファイルを一括で更新するトランザクションではないため、出力途中の失敗時は両方を再生成してください。

## 検査する内容

拡張子による検査対象の絞り込みはありません。拡張子なし、隠しファイル、`.git`以下も走査します。ディレクトリ名・ファイル名に空白や日本語、PowerShellのワイルドカード文字がある場合にも対応します。

| 項目 | 記録内容 |
| --- | --- |
| ファイル | 対象ルートからの相対パス、末尾の拡張子、バイト数、判定状態 |
| 文字コード | 厳密なデコード結果、BOM、判定方法、曖昧な場合の全候補 |
| 改行 | CRLF・LF・CR・NEL（U+0085）・LS（U+2028）・PS（U+2029）の各件数、混在、行数、末尾改行の有無 |
| 検出文字 | U+形式のコードポイント、Unicode名・分類、件数、全出現位置（行・列） |
| 読み取り失敗 | 対象と理由。検査済みの結果と区別して記録 |

Unicode **17.0.0**の定義を同梱し、OSやPython/.NETのUnicodeデータの版に依存しない判定を行います。

- `Cc`：C0/C1制御文字、DEL、NULなど。通常のTABは件数を集計し、CR/LFは改行として集計します。
- `Cf`：ゼロ幅空白、結合子、双方向制御文字、SOFT HYPHEN、ファイル途中のBOMなど。
- `Zs` / `Zl` / `Zp`：NBSP、NARROW NBSP、全角空白、各種特殊空白・改行。通常のSPACE（U+0020）は除外します。
- `Default_Ignorable_Code_Point`：異体字セレクター、タグ文字、Hangul filler、不可視の予約領域など。
- Unicodeの非文字と、REPLACEMENT CHARACTER（U+FFFD）。

**NBSPはUnicode上では制御文字ではなく`Zs`の空白文字ですが、検査対象に含めています。** 正当なインデント、絵文字・異体字、言語の結合子などにも使用されるため、検出を「要確認」として表示します。異なる改行コードの混在も「要確認」です。修正は行いません。

検査はファイル末尾まで行い、サンプルだけで検査を終了したり、検出位置を上限件数で省略したりしません。64KiB単位で入力を読み、デコーダーの状態とCRLFの状態をブロック間で保持します。列はバイト数やUTF-16コード単位ではなくUnicodeコードポイントの数で、TABも1列です。行・列は1始まりで、先頭BOMを除外します。空ファイルは0行、末尾改行の後に架空の空行を加えません。

## 文字コードとテキスト判定

`--encoding` / `-Encoding`には次を指定できます。

```text
auto  utf-8  utf-16le  utf-16be  utf-32le  utf-32be
cp932  euc-jp  iso-2022-jp  windows-1252  latin-1
```

`auto`は、BOM、BOMなしUTF-16/32のASCII部分に基づく推定、ISO-2022-JP識別列、ファイル全体のUTF-8厳密検証を順に使用します。ASCIIのみのUTF-8互換ファイルは`ascii`と表示します。デコード時に不正バイトを置換・無視しません。BOMがあるファイルでデコードに失敗した場合や、指定文字コードとBOMが矛盾する場合はエラーです。

UTF-8として解釈できないファイルはCP932・EUC-JP・Windows-1252・Latin-1の厳密にデコードできる候補を**すべて検査**します。この場合は推定を確定結果として扱わず、検査未確定・終了コード2で報告します。単独の`0xA0`もWestern候補ではNBSPとして検出されます。実際の文字コードを指定して再検査してください。指定は対象ツリー全体に適用されるため、文字コードの異なるファイルがある場合はディレクトリを分けて実行します。

**任意のバイト列から文字コードやテキスト／バイナリの別を100%確定することはできません。** PNG、JPEG、PDF、ZIP（DOCX/XLSXなどを含む）、ELF、PE、gzipなどの既知シグネチャはバイナリとして記録し、内容を文字として検査しません。それ以外はデコード可能性を基に保守的にテキスト候補として検査します。未知のバイナリが候補になることや、BOMのない短い／日本語だけのUTF-16/32などを自動確定できない場合があります。UTF-8を優先する自動判定にも意味的な保証はありません。使用する文字コードが分かっている場合は明示指定が最も確実です。アーカイブ内の展開、PDFからの文章抽出、画像OCRは対象外です。

文字コードを明示指定した場合、対応形式の全バイト・全Unicodeコードポイントを検査します。表示上の不可視性はフォント・文脈にも依存するため、通常の結合文字全般や、私用文字すべてを不可視文字とは判定しません。

シンボリックリンク、Windowsのジャンクションなどのreparse point、デバイス・FIFO・ソケットは、循環や対象ルート外の走査を防ぐため参照せず、未検査項目として記録します。レポート自身の出力予定パス2つは走査から除外し、レポートにそのパスを記載します。読み取りエラー、走査中の変更、未検査項目、曖昧な文字コードがある場合に「検査完了」と表示することはありません。対象ディレクトリを更新しながらの実行は避けてください。

## レポートと終了コード

HTMLをブラウザーで開くと、集計、ファイル検索、状態別フィルター、文字コード候補ごとの詳細、全検出位置を確認できます。外部のスクリプト・CSS・フォントは読み込みません。元ファイルの内容抜粋は保存せず、パスと検出情報を出力します。表示処理は`textContent`を使用し、HTML内のJSONにもエスケープを適用します。

Meiryo UIは閲覧端末にインストールされている場合に使用します。RHEL上でレポートを生成し、Meiryo UIのあるWindows端末で閲覧できます。RHELにフォントがなければ、Meiryo、Noto Sans CJK JP、sans-serifの順にフォールバックします。フォントファイルは同梱しません。[MicrosoftのMeiryo / Meiryo UIの説明](https://learn.microsoft.com/en-us/typography/font-list/meiryo)

| 終了コード | 意味 |
| --- | --- |
| 0 | 検査完了・要確認事項なし。既知バイナリの記録は正常扱い |
| 1 | 検査完了・対象文字または改行混在を検出 |
| 2 | 検査未確定、読み取り・出力エラー、リンクなどの未検査項目、引数エラー |

全検出位置をJSONとHTMLに保持するため、検出件数に比例してメモリ・出力サイズが増えます。大きな結果ではJSONを使った処理も検討してください。

PowerShellホスト自体によるパラメーターバインドエラーの終了コードは、ホストの仕様に従います。

## 開発時の検証

```powershell
python -m unittest discover -s tests -v
python -m compileall -q lib tools tests
```

同じテストデータをPython版と、利用可能なPowerShell版／Linux Bash版へ渡し、文字コード・件数・位置・状態を比較します。Unicode全検査範囲、改行混在、ブロック境界、末尾の不正データ、レガシー文字コードの全変換表、隠しファイル、上書き防止、Windowsのファイルロックなどを検証します。Unicodeデータの更新は開発時のみ`python tools/update_unicode_data.py`で行います。出典とSHA-256を`data/unicode-checks.tsv`に保持しています。[Unicodeデータ](https://www.unicode.org/Public/17.0.0/ucd/UnicodeData.txt)とその派生データには`LICENSE-UNICODE.txt`を同梱しています。レガシー変換表は`python tools/update_legacy_data.py`で生成し、CPythonのライセンスを`LICENSE-PYTHON.txt`に同梱しています。
