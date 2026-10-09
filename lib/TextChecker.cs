// Windows PowerShell 5.1 and PowerShell 7; C# 5 syntax and framework APIs only.
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace ControlCharChecker
{
    using D = Dictionary<string, object>;

    internal sealed class Rule
    {
        public string Kind;
        public string Category;
        public string Name;
        public Rule(string kind, string category, string name)
        {
            Kind = kind; Category = category; Name = name;
        }
    }

    internal sealed class LegacyTables
    {
        private readonly Dictionary<string, Dictionary<int, int>> tables =
            new Dictionary<string, Dictionary<int, int>>();

        public LegacyTables(string path)
        {
            foreach (string line in File.ReadLines(path, Encoding.UTF8))
            {
                if (line.Length == 0 || line[0] == '#') continue;
                string[] fields = line.Split('\t');
                Dictionary<int, int> table;
                if (!tables.TryGetValue(fields[0], out table))
                {
                    table = new Dictionary<int, int>(); tables[fields[0]] = table;
                }
                table.Add(Convert.ToInt32(fields[1], 16), Convert.ToInt32(fields[2], 16));
            }
        }

        public int Lookup(string name, int encoded)
        {
            int point;
            if (!tables[name].TryGetValue(encoded, out point))
                throw new DecoderFallbackException("Invalid byte sequence for " + name + ": 0x" + encoded.ToString("X", CultureInfo.InvariantCulture));
            return point;
        }
    }

    internal sealed class LegacyDecoder
    {
        private readonly string name;
        private readonly LegacyTables tables;
        private readonly List<int> escape = new List<int>();
        private string isoMode = "ascii";
        private int pending, remaining, jisLead = -1;

        public LegacyDecoder(string name, LegacyTables tables)
        {
            this.name = name; this.tables = tables;
        }

        public void Accept(int value, Analysis analysis)
        {
            if (name == "iso-2022-jp") { AcceptIso(value, analysis); return; }
            if (name == "latin-1") { analysis.Accept(value); return; }
            if (name == "windows-1252") { analysis.Accept(tables.Lookup(name, value)); return; }
            if (remaining > 0)
            {
                pending = (pending << 8) | value;
                remaining--;
                if (remaining == 0) analysis.Accept(tables.Lookup(name, pending));
                return;
            }
            if (name == "cp932")
            {
                if ((value >= 0x81 && value <= 0x9F) || (value >= 0xE0 && value <= 0xFC))
                { pending = value; remaining = 1; }
                else analysis.Accept(tables.Lookup(name, value));
            }
            else if (name == "euc-jp")
            {
                if (value <= 0x7F) analysis.Accept(value);
                else if (value == 0x8E || (value >= 0xA1 && value <= 0xFE))
                { pending = value; remaining = 1; }
                else if (value == 0x8F) { pending = value; remaining = 2; }
                else throw new DecoderFallbackException("Invalid lead byte for euc-jp.");
            }
            else throw new ArgumentException("Unsupported legacy encoding: " + name);
        }

        private void AcceptIso(int value, Analysis analysis)
        {
            if (escape.Count > 0)
            {
                escape.Add(value);
                if (escape.Count == 2)
                {
                    if (value != '$' && value != '(' && value != '&')
                    {
                        // A non-designation ESC is an actual control character.
                        escape.Clear(); analysis.Accept(27); AcceptIso(value, analysis);
                    }
                    return;
                }
                if (escape.Count == 3)
                {
                    if (escape[1] == '$' && (value == 'B' || value == '@')) isoMode = "jis208";
                    else if (escape[1] == '(' && (value == 'B' || value == 'J')) isoMode = value == 'J' ? "roman" : "ascii";
                    else if (escape[1] == '&' && value == '@') return;
                    else throw new DecoderFallbackException("Invalid ISO-2022-JP escape designation.");
                    escape.Clear(); return;
                }
                // JIS X 0208:1990 revision announcement ESC & @ ESC $ B.
                if ((escape.Count == 4 && value != 27) || (escape.Count == 5 && value != '$') ||
                    (escape.Count == 6 && value != 'B'))
                    throw new DecoderFallbackException("Invalid ISO-2022-JP revision announcement.");
                if (escape.Count == 6) { isoMode = "jis208"; escape.Clear(); }
                return;
            }
            if (jisLead >= 0)
            {
                if (value < 0x21 || value > 0x7E) throw new DecoderFallbackException("Invalid JIS X 0208 trail byte.");
                analysis.Accept(tables.Lookup("euc-jp", ((jisLead + 0x80) << 8) | (value + 0x80)));
                jisLead = -1; return;
            }
            if (value == 27) { escape.Add(value); return; }
            if (value >= 0x80) throw new DecoderFallbackException("ISO-2022-JP requires seven-bit bytes.");
            if (isoMode == "jis208")
            {
                if (value < 0x20) analysis.Accept(value);
                else if (value >= 0x21 && value <= 0x7E) jisLead = value;
                else throw new DecoderFallbackException("Invalid JIS X 0208 lead byte.");
            }
            else if (isoMode == "roman" && value == 0x5C) analysis.Accept(0xA5);
            else if (isoMode == "roman" && value == 0x7E) analysis.Accept(0x203E);
            else analysis.Accept(value);
        }

        public void Finish()
        {
            if (remaining != 0 || jisLead >= 0 || escape.Count != 0)
                throw new DecoderFallbackException("Incomplete byte sequence at EOF for " + name + ".");
        }
    }

    internal sealed class Rules
    {
        public string Version;
        public readonly LegacyTables Legacy;
        private readonly Dictionary<int, Rule> points = new Dictionary<int, Rule>();
        public Rules(string path)
        {
            var names = new Dictionary<int, string[]>();
            var kinds = new Dictionary<string, string> {
                { "Cc", "control" }, { "Cf", "format" }, { "Zs", "space" },
                { "Zl", "separator" }, { "Zp", "separator" }, { "DI", "ignorable" }
            };
            foreach (string line in File.ReadLines(path, Encoding.UTF8))
            {
                string[] fields = line.Split('\t');
                if (fields[0] == "version") Version = fields[1];
                else if (fields[0] == "range")
                {
                    int first = Convert.ToInt32(fields[2], 16);
                    int last = Convert.ToInt32(fields[3], 16);
                    for (int point = first; point <= last; point++)
                        if (!points.ContainsKey(point))
                            points[point] = new Rule(kinds[fields[1]], fields[1], "");
                }
                else if (fields[0] == "name")
                    names[Convert.ToInt32(fields[1], 16)] = new string[] { fields[2], fields[3] };
            }
            foreach (KeyValuePair<int, Rule> pair in points)
            {
                string[] name;
                bool known = names.TryGetValue(pair.Key, out name);
                pair.Value.Category = known ? name[0] : "Cn";
                pair.Value.Name = known ? name[1] : "UNASSIGNED DEFAULT IGNORABLE";
            }
            Legacy = new LegacyTables(Path.Combine(Path.GetDirectoryName(path), "legacy-mappings.tsv"));
        }

        public Rule Classify(int point)
        {
            if (point == 9 || point == 10 || point == 13 || point == 32) return null;
            if (point == 0xFFFD) return new Rule("replacement", "So", "REPLACEMENT CHARACTER");
            if ((point >= 0xFDD0 && point <= 0xFDEF) || (point & 0xFFFF) == 0xFFFE || (point & 0xFFFF) == 0xFFFF)
                return new Rule("noncharacter", "Cn", "UNICODE NONCHARACTER");
            Rule result;
            return points.TryGetValue(point, out result) ? result : null;
        }
    }

    internal sealed class Analysis
    {
        private readonly Rules rules;
        private readonly Dictionary<int, D> findings = new Dictionary<int, D>();
        private readonly D newlines = new D { { "CRLF", 0L }, { "LF", 0L }, { "CR", 0L },
            { "NEL", 0L }, { "LS", 0L }, { "PS", 0L } };
        private long characters, tabs;
        private long line = 1, column = 1;
        private bool previousCr, endsWithNewline;

        public Analysis(Rules rules) { this.rules = rules; }

        public void Accept(int point)
        {
            characters++;
            Rule rule = rules.Classify(point);
            if (rule != null)
            {
                D finding;
                if (!findings.TryGetValue(point, out finding))
                {
                    finding = new D { { "codepoint", "U+" + point.ToString("X4", CultureInfo.InvariantCulture) },
                        { "name", rule.Name }, { "category", rule.Category }, { "kind", rule.Kind },
                        { "count", 0L }, { "positions", new List<long[]>() } };
                    findings[point] = finding;
                }
                finding["count"] = (long)finding["count"] + 1;
                ((List<long[]>)finding["positions"]).Add(new long[] { line, column });
            }
            if (point == 9) tabs++;
            if (point == 13)
            {
                newlines["CR"] = (long)newlines["CR"] + 1;
                line++; column = 1;
            }
            else if (point == 10)
            {
                if (previousCr)
                {
                    newlines["CR"] = (long)newlines["CR"] - 1;
                    newlines["CRLF"] = (long)newlines["CRLF"] + 1;
                }
                else
                {
                    newlines["LF"] = (long)newlines["LF"] + 1;
                    line++; column = 1;
                }
            }
            else if (point == 0x85 || point == 0x2028 || point == 0x2029)
            {
                string label = point == 0x85 ? "NEL" : (point == 0x2028 ? "LS" : "PS");
                newlines[label] = (long)newlines[label] + 1;
                line++; column = 1;
            }
            else column++;
            previousCr = point == 13;
            endsWithNewline = point == 10 || point == 13 || point == 0x85 || point == 0x2028 || point == 0x2029;
        }

        public D Finish(string encoding)
        {
            long breaks = 0;
            foreach (object value in newlines.Values) breaks += (long)value;
            var keys = new List<int>(findings.Keys);
            keys.Sort();
            var ordered = new List<D>();
            foreach (int point in keys) ordered.Add(findings[point]);
            return new D { { "encoding", encoding }, { "characters", characters },
                { "lines", breaks + (characters > 0 && !endsWithNewline ? 1 : 0) },
                { "newlines", newlines }, { "endsWithNewline", characters == 0 ? null : (object)endsWithNewline },
                { "tabCount", tabs }, { "findings", ordered } };
        }
    }

    public static class Runner
    {
        private const int ChunkSize = 65536;
        private static readonly string[] Legacy = { "cp932", "euc-jp", "windows-1252", "latin-1" };

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MoveFileEx(string existing, string replacement, uint flags);

        private static Encoding DecoderEncoding(string name)
        {
            switch (name)
            {
                case "utf-8": return new UTF8Encoding(false, true);
                case "utf-16le": return new UnicodeEncoding(false, false, true);
                case "utf-16be": return new UnicodeEncoding(true, false, true);
                case "utf-32le": return new UTF32Encoding(false, false, true);
                case "utf-32be": return new UTF32Encoding(true, false, true);
            }
            throw new ArgumentException("Unsupported Unicode encoding: " + name);
        }

        private static D ScanDecoded(FileStream stream, string name, int offset, Rules rules)
        {
            stream.Seek(offset, SeekOrigin.Begin);
            if (!name.StartsWith("utf-", StringComparison.Ordinal))
            {
                var legacy = new LegacyDecoder(name, rules.Legacy);
                var legacyAnalysis = new Analysis(rules);
                var buffer = new byte[ChunkSize];
                int length;
                while ((length = stream.Read(buffer, 0, buffer.Length)) > 0)
                    for (int index = 0; index < length; index++) legacy.Accept(buffer[index], legacyAnalysis);
                legacy.Finish();
                return legacyAnalysis.Finish(name);
            }
            Encoding encoding = DecoderEncoding(name);
            Decoder decoder = encoding.GetDecoder();
            var bytes = new byte[ChunkSize];
            var chars = new char[encoding.GetMaxCharCount(ChunkSize)];
            var analysis = new Analysis(rules);
            char pendingHigh = '\0';
            while (true)
            {
                int count = stream.Read(bytes, 0, bytes.Length);
                int length = decoder.GetChars(bytes, 0, count, chars, 0, count == 0);
                for (int index = 0; index < length; index++)
                {
                    char character = chars[index];
                    if (pendingHigh != '\0')
                    {
                        if (!char.IsLowSurrogate(character)) throw new DecoderFallbackException("Unpaired UTF-16 surrogate.");
                        analysis.Accept(char.ConvertToUtf32(pendingHigh, character));
                        pendingHigh = '\0';
                    }
                    else if (char.IsHighSurrogate(character)) pendingHigh = character;
                    else if (char.IsLowSurrogate(character)) throw new DecoderFallbackException("Unpaired UTF-16 surrogate.");
                    else analysis.Accept(character);
                }
                if (count == 0) break;
            }
            if (pendingHigh != '\0') throw new DecoderFallbackException("Unpaired UTF-16 surrogate.");
            return analysis.Finish(name);
        }

        private static byte[] Probe(FileStream stream, out bool allAscii, out bool hasIso)
        {
            stream.Seek(0, SeekOrigin.Begin);
            var bytes = new byte[ChunkSize];
            byte[] head = null;
            allAscii = true; hasIso = false;
            int previous2 = -1, previous1 = -1;
            int count;
            while ((count = stream.Read(bytes, 0, bytes.Length)) > 0)
            {
                if (head == null)
                {
                    head = new byte[count];
                    Array.Copy(bytes, head, count);
                }
                for (int index = 0; index < count; index++)
                {
                    int current = bytes[index];
                    if (current >= 128) allAscii = false;
                    if (previous2 == 27 && ((previous1 == 36 && (current == 66 || current == 64)) ||
                        (previous1 == 40 && (current == 74 || current == 66)))) hasIso = true;
                    previous2 = previous1; previous1 = current;
                }
            }
            return head ?? new byte[0];
        }

        private static bool Starts(byte[] bytes, params byte[] signature)
        {
            if (bytes.Length < signature.Length) return false;
            for (int index = 0; index < signature.Length; index++)
                if (bytes[index] != signature[index]) return false;
            return true;
        }

        private static string BinaryFormat(FileStream stream, byte[] head)
        {
            if (Starts(head, 137, 80, 78, 71, 13, 10, 26, 10)) return "PNG";
            if (Starts(head, 71, 73, 70, 56, 55, 97) || Starts(head, 71, 73, 70, 56, 57, 97)) return "GIF";
            if (Starts(head, 255, 216, 255)) return "JPEG";
            if (Starts(head, 37, 80, 68, 70, 45)) return "PDF";
            if (Starts(head, 80, 75, 3, 4) || Starts(head, 80, 75, 5, 6) || Starts(head, 80, 75, 7, 8)) return "ZIP";
            if (Starts(head, 127, 69, 76, 70)) return "ELF";
            if (Starts(head, 31, 139)) return "gzip";
            if (Starts(head, 66, 90, 104)) return "bzip2";
            if (Starts(head, 253, 55, 122, 88, 90, 0)) return "xz";
            if (Starts(head, 55, 122, 188, 175, 39, 28)) return "7z";
            if (Starts(head, 82, 97, 114, 33, 26, 7)) return "RAR";
            if (Starts(head, Encoding.ASCII.GetBytes("SQLite format 3\0"))) return "SQLite";
            if (Starts(head, 208, 207, 17, 224, 161, 177, 26, 225)) return "OLE";
            if (Starts(head, 73, 73, 42, 0) || Starts(head, 77, 77, 0, 42)) return "TIFF";
            if (Starts(head, 82, 73, 70, 70) && head.Length >= 12)
            {
                string subtype = Encoding.ASCII.GetString(head, 8, 4);
                if (subtype == "WEBP" || subtype == "WAVE" || subtype == "AVI ") return "RIFF/" + subtype.Trim();
            }
            if (Starts(head, 77, 90) && head.Length >= 64)
            {
                long position = (long)head[60] | ((long)head[61] << 8) | ((long)head[62] << 16) | ((long)head[63] << 24);
                stream.Seek(position, SeekOrigin.Begin);
                var signature = new byte[4];
                if (stream.Read(signature, 0, 4) == 4 && Starts(signature, 80, 69, 0, 0)) return "PE";
            }
            return null;
        }

        private static double Zeros(byte[] bytes, int lane, int width)
        {
            int count = 0, zeros = 0;
            for (int index = lane; index < Math.Min(bytes.Length, 4096); index += width)
            {
                count++;
                if (bytes[index] == 0) zeros++;
            }
            return (double)zeros / count;
        }

        private static string UnicodeHint(byte[] head)
        {
            if (head.Length < 8) return null;
            if (Zeros(head, 2, 4) >= .9 && Zeros(head, 3, 4) >= .9 && Zeros(head, 1, 4) >= .6 && Zeros(head, 0, 4) < .2) return "utf-32le";
            if (Zeros(head, 0, 4) >= .9 && Zeros(head, 1, 4) >= .9 && Zeros(head, 2, 4) >= .6 && Zeros(head, 3, 4) < .2) return "utf-32be";
            if (Zeros(head, 1, 2) >= .6 && Zeros(head, 0, 2) < .2) return "utf-16le";
            if (Zeros(head, 0, 2) >= .6 && Zeros(head, 1, 2) < .2) return "utf-16be";
            return null;
        }

        private static string Extension(string path)
        {
            string name = Path.GetFileName(path);
            int index = name.LastIndexOf('.');
            return index > 0 && index < name.Length - 1 ? name.Substring(index) : "";
        }

        private static string Relative(string path, string root)
        {
            if (String.Equals(path, root, StringComparison.OrdinalIgnoreCase)) return ".";
            string prefix = root.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal)
                ? root : root + Path.DirectorySeparatorChar;
            return path.Substring(prefix.Length).Replace(Path.DirectorySeparatorChar, '/');
        }

        private static D Record(string path, string root, string kind, string reason)
        {
            string status = kind == "text" ? "ok" : (kind == "binary" ? "binary" : (kind == "skipped" ? "skipped" : "error"));
            return new D { { "path", Relative(path, root) }, { "extension", Extension(path) }, { "size", null },
                { "kind", kind }, { "status", status }, { "reason", reason }, { "bom", "" },
                { "encodingSource", "" }, { "encodings", new List<string>() }, { "analyses", new List<D>() } };
        }

        private static bool NeedsReview(D analysis)
        {
            if (((List<D>)analysis["findings"]).Count > 0) return true;
            int types = 0;
            foreach (object value in ((D)analysis["newlines"]).Values)
                if ((long)value > 0) types++;
            return types > 1;
        }

        private static D CheckFile(string path, string root, string requestedEncoding, Rules rules)
        {
            D result = Record(path, root, "text", "");
            try
            {
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("The entry became a symbolic link/reparse point.");
                var before = new FileInfo(path);
                long beforeLength = before.Length;
                long beforeTime = before.LastWriteTimeUtc.Ticks;
                long beforeCreation = before.CreationTimeUtc.Ticks;
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read,
                    ChunkSize, FileOptions.SequentialScan))
                {
                    result["size"] = stream.Length;
                    bool allAscii, hasIso;
                    byte[] head = Probe(stream, out allAscii, out hasIso);
                    string binary = BinaryFormat(stream, head);
                    if (binary != null)
                    {
                        result["kind"] = "binary"; result["status"] = "binary";
                        result["reason"] = "Known binary signature: " + binary;
                    }
                    else
                    {
                        string bom = "";
                        int offset = 0;
                        if (Starts(head, 255, 254, 0, 0)) { bom = "utf-32le"; offset = 4; }
                        else if (Starts(head, 0, 0, 254, 255)) { bom = "utf-32be"; offset = 4; }
                        else if (Starts(head, 239, 187, 191)) { bom = "utf-8"; offset = 3; }
                        else if (Starts(head, 255, 254)) { bom = "utf-16le"; offset = 2; }
                        else if (Starts(head, 254, 255)) { bom = "utf-16be"; offset = 2; }
                        result["bom"] = bom;
                        string name, source;
                        if (requestedEncoding != "auto")
                        {
                            if (bom != "" && requestedEncoding != bom)
                                throw new ArgumentException("Requested encoding conflicts with BOM (" + bom + ").");
                            name = requestedEncoding; source = "explicit";
                        }
                        else if (bom != "") { name = bom; source = "bom"; }
                        else if (UnicodeHint(head) != null) { name = UnicodeHint(head); source = "heuristic"; }
                        else if (hasIso && allAscii) { name = "iso-2022-jp"; source = "iso-2022-marker"; }
                        else { name = "utf-8"; source = "utf-8-validation"; }
                        var analyses = new List<D>();
                        try { analyses.Add(ScanDecoded(stream, name, offset, rules)); }
                        catch (DecoderFallbackException)
                        {
                            if (source != "utf-8-validation") throw;
                            source = "ambiguous";
                            foreach (string candidate in Legacy)
                            {
                                try { analyses.Add(ScanDecoded(stream, candidate, 0, rules)); }
                                catch (DecoderFallbackException) { /* Invalid candidate; retained alternatives are reported. */ }
                            }
                        }
                        if (analyses.Count == 0) throw new ArgumentException("No supported strict decoder accepted the file.");
                        if (source == "utf-8-validation" && allAscii) analyses[0]["encoding"] = "ascii";
                        var encodings = new List<string>();
                        bool review = source == "ambiguous";
                        foreach (D analysis in analyses)
                        {
                            encodings.Add((string)analysis["encoding"]);
                            review |= NeedsReview(analysis);
                        }
                        result["analyses"] = analyses; result["encodings"] = encodings;
                        result["encodingSource"] = source;
                        if (source == "ambiguous")
                            result["reason"] = "Encoding is ambiguous; all listed interpretations were checked. Specify --encoding.";
                        if (review) result["status"] = "warning";
                    }
                    var after = new FileInfo(path);
                    if (beforeLength != stream.Length || beforeLength != after.Length ||
                        beforeTime != after.LastWriteTimeUtc.Ticks || beforeCreation != after.CreationTimeUtc.Ticks ||
                        (after.Attributes & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("The file changed during inspection; rerun on a stable directory.");
                }
            }
            catch (Exception error)
            {
                if (!(error is IOException || error is UnauthorizedAccessException || error is ArgumentException ||
                    error is NotSupportedException || error is System.Security.SecurityException)) throw;
                result["kind"] = "error"; result["status"] = "error"; result["reason"] = error.Message;
            }
            return result;
        }

        private static List<D> ScanTree(string root, HashSet<string> excluded, string encoding, Rules rules)
        {
            var results = new List<D>();
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0)
            {
                string directory = pending.Pop();
                string[] children;
                try { children = Directory.GetFileSystemEntries(directory); }
                catch (Exception error)
                {
                    if (!(error is IOException || error is UnauthorizedAccessException || error is System.Security.SecurityException)) throw;
                    results.Add(Record(directory, root, "error", error.Message));
                    continue;
                }
                Array.Sort(children, StringComparer.Ordinal);
                foreach (string path in children)
                {
                    if (excluded.Contains(path)) continue;
                    try
                    {
                        FileAttributes attributes = File.GetAttributes(path);
                        if ((attributes & FileAttributes.ReparsePoint) != 0)
                            results.Add(Record(path, root, "skipped", "Symbolic link/reparse point; target not followed."));
                        else if ((attributes & FileAttributes.Directory) != 0) pending.Push(path);
                        else results.Add(CheckFile(path, root, encoding, rules));
                    }
                    catch (Exception error)
                    {
                        if (!(error is IOException || error is UnauthorizedAccessException || error is System.Security.SecurityException)) throw;
                        results.Add(Record(path, root, "error", error.Message));
                    }
                }
            }
            results.Sort(delegate(D left, D right) { return StringComparer.Ordinal.Compare((string)left["path"], (string)right["path"]); });
            return results;
        }

        private static D MakeReport(string root, string output, string encoding, List<D> records, Rules rules)
        {
            var summary = new D { { "entries", (long)records.Count }, { "text", 0L }, { "binary", 0L },
                { "skipped", 0L }, { "ok", 0L }, { "warning", 0L }, { "error", 0L }, { "ambiguous", 0L } };
            foreach (D record in records)
            {
                string kind = (string)record["kind"], status = (string)record["status"];
                if (kind == "text" || kind == "binary" || kind == "skipped") summary[kind] = (long)summary[kind] + 1;
                if (status == "ok" || status == "warning" || status == "error") summary[status] = (long)summary[status] + 1;
                if ((string)record["encodingSource"] == "ambiguous") summary["ambiguous"] = (long)summary["ambiguous"] + 1;
            }
            bool complete = (long)summary["error"] == 0 && (long)summary["skipped"] == 0 && (long)summary["ambiguous"] == 0;
            return new D { { "schemaVersion", 1 }, { "unicodeVersion", rules.Version }, { "root", root },
                { "generatedAt", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) },
                { "requestedEncoding", encoding }, { "complete", complete },
                { "excludedOutputs", new string[] { output, Path.ChangeExtension(output, ".json") } },
                { "summary", summary }, { "files", records } };
        }

        private static void JsonString(TextWriter writer, string value)
        {
            writer.Write('"');
            foreach (char character in value)
            {
                if (character == '"') writer.Write("\\\"");
                else if (character == '\\') writer.Write("\\\\");
                else if (character < 32 || character > 126 || character == '<' || character == '>' || character == '&')
                    writer.Write("\\u" + ((int)character).ToString("x4", CultureInfo.InvariantCulture));
                else writer.Write(character);
            }
            writer.Write('"');
        }

        private static void JsonValue(TextWriter writer, object value)
        {
            if (value == null) { writer.Write("null"); return; }
            if (value is string) { JsonString(writer, (string)value); return; }
            if (value is bool) { writer.Write((bool)value ? "true" : "false"); return; }
            var dictionary = value as IDictionary;
            if (dictionary != null)
            {
                writer.Write('{'); bool first = true;
                foreach (DictionaryEntry pair in dictionary)
                {
                    if (!first) writer.Write(',');
                    first = false; JsonString(writer, (string)pair.Key); writer.Write(':'); JsonValue(writer, pair.Value);
                }
                writer.Write('}'); return;
            }
            var sequence = value as IEnumerable;
            if (sequence != null)
            {
                writer.Write('['); bool first = true;
                foreach (object item in sequence)
                {
                    if (!first) writer.Write(',');
                    first = false; JsonValue(writer, item);
                }
                writer.Write(']'); return;
            }
            if (value is long || value is int) { writer.Write(Convert.ToString(value, CultureInfo.InvariantCulture)); return; }
            throw new ArgumentException("Unexpected JSON value type: " + value.GetType().FullName);
        }

        private static void WriteReports(D report, string output, bool force, string toolRoot)
        {
            string payload;
            using (var writer = new StringWriter(CultureInfo.InvariantCulture))
            {
                JsonValue(writer, report); payload = writer.ToString();
            }
            string template = File.ReadAllText(Path.Combine(toolRoot, "assets", "report.html"), Encoding.UTF8);
            const string placeholder = "@@REPORT_JSON@@";
            int start = template.IndexOf(placeholder, StringComparison.Ordinal);
            if (start < 0 || template.IndexOf(placeholder, start + placeholder.Length, StringComparison.Ordinal) >= 0)
                throw new ArgumentException("The report template must contain exactly one data placeholder.");
            string[] paths = { output, Path.ChangeExtension(output, ".json") };
            string[] contents = { template.Replace(placeholder, payload), payload + "\n" };
            var temporary = new List<string>();
            try
            {
                for (int index = 0; index < paths.Length; index++)
                {
                    string directory = Path.GetDirectoryName(paths[index]);
                    Directory.CreateDirectory(directory);
                    string path = Path.Combine(directory, ".text-check-" + Guid.NewGuid().ToString("N") + ".tmp");
                    temporary.Add(path);
                    using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    using (var writer = new StreamWriter(file, new UTF8Encoding(false)))
                        writer.Write(contents[index]);
                }
                for (int index = 0; index < paths.Length; index++)
                {
                    // ReplaceFile merges ACL metadata and may require WRITE_DAC.
                    // Rename-with-replacement is sufficient for generated reports.
                    if (force && Environment.OSVersion.Platform == PlatformID.Win32NT)
                    {
                        if (!MoveFileEx(temporary[index], paths[index], 1U | 8U))
                            throw new Win32Exception(Marshal.GetLastWin32Error());
                    }
                    else if (force && File.Exists(paths[index])) File.Replace(temporary[index], paths[index], null);
                    else File.Move(temporary[index], paths[index]);
                }
            }
            finally
            {
                foreach (string path in temporary) if (File.Exists(path)) File.Delete(path);
            }
        }

        private static void CheckOutput(string path, bool force)
        {
            try
            {
                FileAttributes attributes = File.GetAttributes(path);
                if ((attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                    throw new ArgumentException("Output is a directory or symbolic link: " + path);
                if (!force) throw new ArgumentException("Output exists; use -Force to replace it: " + path);
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
        }

        public static int Run(string input, string output, string encoding, bool force, string toolRoot)
        {
            string root = Path.GetFullPath(input);
            if (root != Path.GetPathRoot(root)) root = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            output = Path.GetFullPath(output);
            if (!Directory.Exists(root)) throw new ArgumentException("The input path must be a directory.");
            if (!String.Equals(Path.GetExtension(output), ".html", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("-Output must end with .html.");
            string jsonOutput = Path.ChangeExtension(output, ".json");
            CheckOutput(output, force); CheckOutput(jsonOutput, force);
            var rules = new Rules(Path.Combine(toolRoot, "data", "unicode-checks.tsv"));
            var excluded = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { output, jsonOutput };
            List<D> records = ScanTree(root, excluded, encoding, rules);
            D report = MakeReport(root, output, encoding, records, rules);
            WriteReports(report, output, force, toolRoot);
            D summary = (D)report["summary"];
            Console.WriteLine("HTML: " + output);
            Console.WriteLine("JSON: " + jsonOutput);
            Console.WriteLine("Entries={0} Text={1} Binary={2} Review={3} Errors={4} Skipped={5}",
                summary["entries"], summary["text"], summary["binary"], summary["warning"], summary["error"], summary["skipped"]);
            return !(bool)report["complete"] ? 2 : ((long)summary["warning"] > 0 ? 1 : 0);
        }
    }
}
