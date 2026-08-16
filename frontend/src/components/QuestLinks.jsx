const WIKQ2 = 'https://wikq2.jupiterns.org/'

export const wikiQuestUrl = (page) => `https://eq2.fandom.com/wiki/${String(page).replace(/ /g, '_')}`
export const wikq2QuestUrl = (page) => `${WIKQ2}?q=${encodeURIComponent(page)}`

export default function QuestLinks({ page }) {
  return (
    <span className="questlinks" aria-label={`Links for ${page}`}>
      <a href={wikq2QuestUrl(page)} target="_blank" rel="noreferrer noopener"
         title="Open in wikQ2" aria-label={`${page} in wikQ2`}
         onClick={(event) => event.stopPropagation()}>wikq2</a>
      <a href={wikiQuestUrl(page)} target="_blank" rel="noreferrer noopener"
         title="Open in EQ2 Wiki" aria-label={`${page} in EQ2 Wiki`}
         onClick={(event) => event.stopPropagation()}>EQ2i</a>
    </span>
  )
}
