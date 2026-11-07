// The landing page's reads: the compiler's own passes and effect rules. Built into the page from the engine
// (landing/engineData.ts), so the sections never depend on a server being up.
import { PASSES, LATTICE } from '../landing/engineData';

export const usePasses = () => ({ data: { passes: PASSES }, error: null, isLoading: false, isError: false });
export const useLattice = () => ({ data: LATTICE, error: null, isLoading: false, isError: false });
