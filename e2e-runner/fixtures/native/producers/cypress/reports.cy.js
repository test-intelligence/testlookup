describe('release fixture', () => {
 it('passes', () => expect(2 + 2).to.equal(4))
 it('fails', () => expect(1, 'release fixture expected failure').to.equal(2))
 it.skip('skips', () => {})
})
