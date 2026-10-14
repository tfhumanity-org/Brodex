// DecompileSelect.java — Ghidra headless POSTSCRIPT.
//
// Decompiles a SELECTED subset of functions to C and writes them under
// $CU_OUT_DIR/decomp/<name>__<entry>.c plus an index TSV. Selection comes from
// any combination of three sources (a function is decompiled if it matches ANY):
//
//   1. Explicit addresses — $CU_TARGETS: a TSV whose first column is an entry
//      address in hex (Ghidra form, e.g. 00000001000989c or 0x10000989c), with
//      an optional second column used as a human label in the output header.
//   2. Name keywords — $CU_NAME_KEYWORDS: comma-separated, case-insensitive
//      substrings matched against each function's (demangled) name.
//   3. String-xref keywords — $CU_STRING_KEYWORDS: comma-separated,
//      case-insensitive substrings; any function that references a defined
//      string literal containing one of them is selected.
//
// If none of the three are set, nothing is decompiled (run ExportFunctions
// first, pick targets, then re-run with a selection). This keeps the pass fast
// and intentional instead of decompiling all ~tens-of-thousands of functions.
//
// Env:
//   CU_OUT_DIR         output dir (default /tmp/cu_out). Writes decomp/ + index.
//   CU_TARGETS         path to address TSV (optional)
//   CU_NAME_KEYWORDS   comma-separated name substrings (optional)
//   CU_STRING_KEYWORDS comma-separated string substrings (optional)
//   CU_MAX             cap on functions decompiled (default 400)
//   CU_DECOMP_TIMEOUT  per-function decompiler timeout, seconds (default 90)
//   CU_MAX_LINES       cap on lines written per function (default 600)
//
// Class name MUST match the filename (Ghidra requirement).

import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class DecompileSelect extends GhidraScript {

    @Override
    public void run() throws Exception {
        if (currentProgram == null) {
            println("DecompileSelect: no current program.");
            return;
        }
        String outDir = System.getenv("CU_OUT_DIR");
        if (outDir == null || outDir.isEmpty()) outDir = "/tmp/cu_out";
        File decompDir = new File(outDir, "decomp");
        decompDir.mkdirs();

        int maxFuncs = envInt("CU_MAX", 400);
        int timeout = envInt("CU_DECOMP_TIMEOUT", 90);
        int maxLines = envInt("CU_MAX_LINES", 600);
        List<String> nameKw = envList("CU_NAME_KEYWORDS");
        List<String> strKw = envList("CU_STRING_KEYWORDS");
        String targetsFile = System.getenv("CU_TARGETS");

        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();

        // Selection: entry-offset -> label (label may be null)
        LinkedHashMap<Long, String> selected = new LinkedHashMap<>();

        // (1) explicit addresses
        int fromAddr = selectByAddresses(targetsFile, fm, selected);
        // (2) name keywords
        int fromName = 0;
        // (3) string keywords
        int fromStr = 0;
        if (!nameKw.isEmpty() || !strKw.isEmpty()) {
            for (Function f : fm.getFunctions(true)) {
                if (monitor.isCancelled()) break;
                long key = f.getEntryPoint().getOffset();
                if (selected.containsKey(key)) continue;
                if (!nameKw.isEmpty() && matchesName(f, nameKw)) {
                    selected.put(key, null); fromName++; continue;
                }
                if (!strKw.isEmpty() && matchesString(f, listing, strKw)) {
                    selected.put(key, null); fromStr++;
                }
            }
        }

        println("DecompileSelect: selected " + selected.size()
                + " (addresses=" + fromAddr + ", name=" + fromName + ", string=" + fromStr + ")");
        if (selected.isEmpty()) {
            println("DecompileSelect: no selection — set CU_TARGETS / CU_NAME_KEYWORDS / CU_STRING_KEYWORDS.");
            return;
        }

        DecompInterface di = new DecompInterface();
        try {
            di.openProgram(currentProgram);
            BufferedWriter idx = new BufferedWriter(new FileWriter(new File(outDir, "decomp_index.tsv")));
            idx.write("file\tentry\tname\tlabel\n");
            Set<String> usedNames = new HashSet<>();
            int done = 0;

            for (Map.Entry<Long, String> e : selected.entrySet()) {
                if (done >= maxFuncs) { println("DecompileSelect: hit CU_MAX=" + maxFuncs); break; }
                if (monitor.isCancelled()) break;

                Address a = currentProgram.getAddressFactory()
                        .getDefaultAddressSpace().getAddress(e.getKey());
                Function f = fm.getFunctionAt(a);
                if (f == null) f = fm.getFunctionContaining(a);
                if (f == null) { println("  no function at " + a); continue; }

                String label = e.getValue();
                String name;
                try { name = f.getName(true); } catch (Exception ex) { name = f.getName(); }

                DecompileResults res;
                try {
                    res = di.decompileFunction(f, timeout, monitor);
                } catch (Exception ex) {
                    println("  decompile error " + name + ": " + ex.getMessage());
                    continue;
                }
                if (res == null || !res.decompileCompleted()) {
                    println("  decompile incomplete: " + name);
                    continue;
                }
                String code = res.getDecompiledFunction().getC();
                code = capLines(code, maxLines);

                String fname = uniqueName(f, usedNames);
                BufferedWriter w = new BufferedWriter(new FileWriter(new File(decompDir, fname)));
                w.write("// name: " + name + "\n");
                if (label != null) w.write("// label: " + label + "\n");
                w.write("// entry: " + f.getEntryPoint() + "\n\n");
                w.write(code);
                w.close();

                idx.write(fname + "\t" + f.getEntryPoint() + "\t"
                        + name.replace('\t', ' ').replace('\n', ' ') + "\t"
                        + (label == null ? "" : label.replace('\t', ' ').replace('\n', ' ')) + "\n");
                done++;
            }
            idx.close();
            println("DecompileSelect: decompiled " + done + " function(s) to " + decompDir.getPath());
        } finally {
            di.dispose();
        }
    }

    private int selectByAddresses(String targetsFile, FunctionManager fm,
                                  LinkedHashMap<Long, String> selected) {
        if (targetsFile == null || targetsFile.isEmpty()) return 0;
        File tf = new File(targetsFile);
        if (!tf.exists()) { println("DecompileSelect: CU_TARGETS not found: " + targetsFile); return 0; }
        int n = 0;
        try (BufferedReader br = new BufferedReader(new FileReader(tf))) {
            String ln;
            boolean first = true;
            while ((ln = br.readLine()) != null) {
                if (ln.trim().isEmpty()) continue;
                String[] p = ln.split("\t", 2);
                String addrStr = p[0].trim();
                // skip an obvious header line
                if (first && addrStr.equalsIgnoreCase("entry")) { first = false; continue; }
                first = false;
                if (addrStr.startsWith("0x") || addrStr.startsWith("0X")) addrStr = addrStr.substring(2);
                long off;
                try { off = Long.parseLong(addrStr, 16); }
                catch (Exception ex) { println("  bad address: " + p[0]); continue; }
                String label = (p.length > 1) ? p[1].trim() : null;
                if (!selected.containsKey(off)) { selected.put(off, label); n++; }
            }
        } catch (IOException ex) {
            println("DecompileSelect: error reading CU_TARGETS: " + ex.getMessage());
        }
        return n;
    }

    private boolean matchesName(Function f, List<String> kws) {
        String nm;
        try { nm = f.getName(true); } catch (Exception e) { nm = f.getName(); }
        if (nm == null) return false;
        String low = nm.toLowerCase();
        for (String k : kws) if (low.contains(k)) return true;
        return false;
    }

    private boolean matchesString(Function f, Listing listing, List<String> kws) {
        InstructionIterator it = listing.getInstructions(f.getBody(), true);
        Set<Address> seen = new HashSet<>();
        while (it.hasNext()) {
            Instruction insn = it.next();
            for (Reference r : insn.getReferencesFrom()) {
                Address to = r.getToAddress();
                if (to == null || !seen.add(to)) continue;
                Data d = listing.getDataAt(to);
                if (d == null) continue;
                String s = asString(d);
                if (s == null) continue;
                String low = s.toLowerCase();
                for (String k : kws) if (low.contains(k)) return true;
            }
        }
        return false;
    }

    private String asString(Data d) {
        try {
            if (d.hasStringValue()) {
                Object v = d.getValue();
                if (v != null) return v.toString();
            }
            StringDataInstance sdi = StringDataInstance.getStringDataInstance(d);
            if (sdi != null && sdi.getStringLength() > 0) return sdi.getStringValue();
        } catch (Exception e) { /* not a string */ }
        return null;
    }

    private String capLines(String code, int maxLines) {
        String[] lines = code.split("\n", -1);
        if (lines.length <= maxLines) return code;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < maxLines; i++) sb.append(lines[i]).append('\n');
        sb.append("// ... truncated at ").append(maxLines)
          .append(" lines (").append(lines.length).append(" total)\n");
        return sb.toString();
    }

    private String uniqueName(Function f, Set<String> used) {
        String base;
        try { base = f.getName(false); } catch (Exception e) { base = f.getName(); }
        base = base.replaceAll("[^A-Za-z0-9._-]", "_");
        if (base.length() > 120) base = base.substring(0, 120);
        String fname = base + "__" + f.getEntryPoint().toString() + ".c";
        if (fname.length() > 180) fname = fname.substring(0, 176) + ".c";
        int i = 1;
        String cand = fname;
        while (used.contains(cand)) { cand = fname.replaceAll("\\.c$", "") + "_" + (i++) + ".c"; }
        used.add(cand);
        return cand;
    }

    private int envInt(String name, int dflt) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return dflt;
        try { return Integer.parseInt(v.trim()); } catch (Exception e) { return dflt; }
    }

    private List<String> envList(String name) {
        List<String> out = new ArrayList<>();
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return out;
        for (String part : v.split(",")) {
            String t = part.trim().toLowerCase();
            if (!t.isEmpty()) out.add(t);
        }
        return out;
    }
}
