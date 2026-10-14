// ExportFunctions.java — Ghidra headless POSTSCRIPT.
//
// Dumps one TSV row per function to $CU_OUT_DIR/functions.tsv (default
// /tmp/cu_out). This is the inventory the DecompileSelect pass (and a human)
// uses to pick interesting functions without re-opening Ghidra.
//
// Columns (tab-separated, one header row):
//   entry       entry-point address (hex, Ghidra form)
//   size        function body size in bytes
//   name        raw symbol name
//   demangled   demangled name (Swift/C++), or "" if same as raw / unavailable
//   namespace   parent namespace path (e.g. ComputerUse::Foo), or "" for global
//   xrefs       number of references TO the entry point (approx. caller count)
//   strings     up to MAX_STRINGS referenced string literals, "|"-joined,
//               each truncated to STRING_CAP chars (newlines/tabs stripped)
//
// Env:
//   CU_OUT_DIR  output directory (created if missing). Default /tmp/cu_out.
//
// Class name MUST match the filename (Ghidra requirement).

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class ExportFunctions extends GhidraScript {

    private static final int MAX_STRINGS = 6;   // string literals listed per function
    private static final int STRING_CAP = 80;   // max chars per listed string

    @Override
    public void run() throws Exception {
        if (currentProgram == null) {
            println("ExportFunctions: no current program.");
            return;
        }
        String outDir = System.getenv("CU_OUT_DIR");
        if (outDir == null || outDir.isEmpty()) outDir = "/tmp/cu_out";
        new File(outDir).mkdirs();

        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Listing listing = currentProgram.getListing();

        File outFile = new File(outDir, "functions.tsv");
        BufferedWriter w = new BufferedWriter(new FileWriter(outFile));
        w.write("entry\tsize\tname\tdemangled\tnamespace\txrefs\tstrings\n");

        int count = 0;
        for (Function f : fm.getFunctions(true)) {
            if (monitor.isCancelled()) break;

            Address entry = f.getEntryPoint();
            long size = f.getBody().getNumAddresses();

            String raw = f.getName(false);
            String demangled = "";
            try {
                String full = f.getName(true); // includes namespace + demangled form
                if (full != null && !full.equals(raw)) demangled = full;
            } catch (Exception e) { /* leave blank */ }

            String ns = "";
            try {
                Namespace parent = f.getParentNamespace();
                if (parent != null && !parent.isGlobal()) {
                    ns = parent.getName(true);
                }
            } catch (Exception e) { /* leave blank */ }

            int xrefs = 0;
            try {
                for (Reference r : rm.getReferencesTo(entry)) xrefs++;
            } catch (Exception e) { /* leave 0 */ }

            String strings = collectStrings(f, listing);

            w.write(entry.toString());
            w.write('\t'); w.write(Long.toString(size));
            w.write('\t'); w.write(clean(raw));
            w.write('\t'); w.write(clean(demangled));
            w.write('\t'); w.write(clean(ns));
            w.write('\t'); w.write(Integer.toString(xrefs));
            w.write('\t'); w.write(clean(strings));
            w.write('\n');
            count++;
        }
        w.close();
        println("ExportFunctions: wrote " + count + " functions to " + outFile.getPath());
    }

    // Walk the function's outgoing references; collect the first MAX_STRINGS
    // that land on a defined string literal.
    private String collectStrings(Function f, Listing listing) {
        List<String> found = new ArrayList<>();
        Set<Address> seen = new HashSet<>();
        AddressSetView body = f.getBody();

        InstructionIterator it = listing.getInstructions(body, true);
        outer:
        while (it.hasNext()) {
            Instruction insn = it.next();
            for (Reference r : insn.getReferencesFrom()) {
                Address to = r.getToAddress();
                if (to == null || seen.contains(to)) continue;
                seen.add(to);
                Data d = listing.getDataAt(to);
                if (d == null) continue;
                String s = asString(d);
                if (s == null) continue;
                found.add(truncate(s, STRING_CAP));
                if (found.size() >= MAX_STRINGS) break outer;
            }
        }
        return String.join("|", found);
    }

    private String asString(Data d) {
        try {
            if (d.hasStringValue()) {
                Object v = d.getValue();
                if (v != null) return v.toString();
            }
            StringDataInstance sdi = StringDataInstance.getStringDataInstance(d);
            if (sdi != null && sdi.getStringLength() > 0) {
                return sdi.getStringValue();
            }
        } catch (Exception e) { /* not a string */ }
        return null;
    }

    private String truncate(String s, int n) {
        if (s == null) return "";
        s = s.trim();
        if (s.length() <= n) return s;
        return s.substring(0, n) + "…";
    }

    private String clean(String s) {
        if (s == null) return "";
        return s.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }
}
