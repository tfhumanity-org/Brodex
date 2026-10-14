// FastAnalysisPre.java — Ghidra headless PRESCRIPT.
//
// Runs before auto-analysis. Disables analyzers that stall for many minutes
// (or effectively hang) on a large (~23 MB) Swift/ObjC Mach-O, so the rest of
// the pipeline (ExportFunctions / DecompileSelect) can run in reasonable time.
//
// Disabled unconditionally (these are the expensive ones for big Swift binaries):
//   - Decompiler Parameter ID   (re-decompiles every function to recover params)
//   - Decompiler Switch Analysis (decompiler-driven jump-table recovery)
//   - Call Convention ID / Call Convention Identification
//   - Stack / Shared Return Calls / Aggressive Instruction Finder
//     (cheap individually, but noisy and occasionally pathological here)
//
// Objective-C message analysis is OPTIONAL and controlled by a script argument:
//   pass "noobjc" (the first script arg) to also disable the Objective-C
//   analyzers. Default (no arg) leaves ObjC message analysis ON, because it is
//   what resolves `objc_msgSend` dispatch for the ObjC side of the app.
//
// Every option is toggled in a try/catch: analyzer display names vary slightly
// across Ghidra versions, so an unknown name is logged and skipped rather than
// aborting the run. The class name MUST match the filename (Ghidra requirement).

import ghidra.app.script.GhidraScript;

public class FastAnalysisPre extends GhidraScript {

    // Expensive analyzers disabled on every run.
    private static final String[] ALWAYS_OFF = {
        "Decompiler Parameter ID",
        "Decompiler Switch Analysis",
        "Call Convention ID",
        "Call Convention Identification",
        "Stack",
        "Shared Return Calls",
        "Aggressive Instruction Finder",
        "Variadic Function Signature Override",
    };

    // Objective-C analyzers, only disabled when "noobjc" is passed.
    private static final String[] OBJC_OFF = {
        "Objective-C 2 Message",
        "Objective-C 2 Decompiler Message",
        "Objective-C 2 Class",
    };

    @Override
    public void run() throws Exception {
        if (currentProgram == null) {
            println("FastAnalysisPre: no current program; nothing to do.");
            return;
        }

        boolean dropObjc = false;
        for (String a : getScriptArgs()) {
            if (a != null && a.trim().equalsIgnoreCase("noobjc")) {
                dropObjc = true;
            }
        }

        int disabled = 0;
        for (String opt : ALWAYS_OFF) {
            if (disable(opt)) disabled++;
        }
        if (dropObjc) {
            println("FastAnalysisPre: 'noobjc' arg present — disabling Objective-C message analysis.");
            for (String opt : OBJC_OFF) {
                if (disable(opt)) disabled++;
            }
        } else {
            println("FastAnalysisPre: Objective-C message analysis left ENABLED (pass 'noobjc' to disable).");
        }
        println("FastAnalysisPre: disabled " + disabled + " analyzer option(s).");
    }

    private boolean disable(String optionName) {
        try {
            setAnalysisOption(currentProgram, optionName, "false");
            println("  disabled: " + optionName);
            return true;
        } catch (Exception e) {
            println("  skip (not present): " + optionName + " — " + e.getMessage());
            return false;
        }
    }
}
