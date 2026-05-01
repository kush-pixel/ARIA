export const ANTIHYPERTENSIVE_KEYWORDS: readonly string[] = [
  // ACE inhibitors
  'lisinopril', 'enalapril', 'ramipril', 'benazepril', 'captopril',
  'fosinopril', 'quinapril', 'perindopril', 'trandolapril', 'moexipril',
  // ARBs
  'losartan', 'valsartan', 'irbesartan', 'candesartan', 'olmesartan',
  'telmisartan', 'azilsartan', 'eprosartan',
  // Beta-blockers
  'metoprolol', 'atenolol', 'bisoprolol', 'carvedilol', 'propranolol',
  'nadolol', 'labetalol', 'nebivolol', 'acebutolol',
  // Calcium channel blockers
  'amlodipine', 'diltiazem', 'verapamil', 'nifedipine', 'felodipine',
  'nicardipine', 'nisoldipine', 'isradipine',
  // Thiazide / thiazide-like diuretics
  'hydrochlorothiazide', 'hctz', 'chlorthalidone', 'indapamide', 'metolazone',
  // Loop diuretics (used in HTN+CHF)
  'furosemide', 'torsemide', 'bumetanide', 'lasix',
  // Potassium-sparing / aldosterone antagonists
  'spironolactone', 'eplerenone', 'triamterene', 'amiloride', 'aldactone',
  // Alpha-blockers
  'doxazosin', 'prazosin', 'terazosin', 'cardura',
  // Central agents
  'clonidine', 'methyldopa', 'guanfacine', 'catapres',
  // Direct vasodilators
  'hydralazine', 'minoxidil',
  // Renin inhibitors
  'aliskiren', 'tekturna',
  // Common brand names
  'norvasc', 'toprol', 'lopressor', 'zestril', 'prinivil', 'altace',
  'cozaar', 'diovan', 'benicar', 'avapro', 'atacand', 'micardis',
  'tenormin', 'inderal', 'coreg', 'cardizem', 'calan', 'adalat',
]

/**
 * Returns true if `name` contains any antihypertensive keyword at a word boundary.
 * Case-insensitive. Uses \b to avoid false matches (e.g. "amiloride" ≠ "amiodarone").
 */
export function isHypertensionMedication(name: string): boolean {
  const lower = name.toLowerCase()
  return ANTIHYPERTENSIVE_KEYWORDS.some((kw) => {
    // \b may not work reliably at non-ASCII boundaries, but all keywords are ASCII
    const pattern = new RegExp(`\\b${kw}\\b`)
    return pattern.test(lower)
  })
}

/**
 * Filters the "Current regimen: ..." sentence in a medication_status string to show
 * only antihypertensive medications. Non-regimen sentences (last change date, titration
 * notices) are preserved verbatim. Returns the original string if format is unexpected.
 */
export function filterMedicationStatusText(text: string | undefined): string {
  if (!text) return ''
  if (!text.startsWith('Current regimen:')) return text

  const parts = text.split('. ')
  const regimenSentence = parts[0]
  const tail = parts.slice(1).join('. ')

  const prefix = 'Current regimen: '
  const medList = regimenSentence.slice(prefix.length)
  const meds = medList.split(', ')
  const filtered = meds.filter((m) => isHypertensionMedication(m))

  const regimenPart =
    filtered.length > 0
      ? `${prefix}${filtered.join(', ')}`
      : `${prefix}No antihypertensive medications identified`

  return tail ? `${regimenPart}. ${tail}` : regimenPart
}
