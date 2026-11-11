import org.testng.annotations.Test;
import static org.testng.Assert.assertEquals;

public class TestNgFixture {
    @Test public void release_pass() { assertEquals(2 + 2, 4); }
    @Test public void release_fail() { assertEquals(2 + 2, 5); }
}
