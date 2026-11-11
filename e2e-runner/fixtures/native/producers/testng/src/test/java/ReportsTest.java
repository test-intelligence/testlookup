import org.testng.Assert;
import org.testng.SkipException;
import org.testng.annotations.Test;
public class ReportsTest {
 @Test public void passes() { Assert.assertEquals(2 + 2, 4); }
 @Test public void fails() { Assert.fail("release fixture expected failure"); }
 @Test public void skips() { throw new SkipException("release fixture skip"); }
}
