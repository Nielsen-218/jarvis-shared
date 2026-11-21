"""The built-in IT fundamentals course: a concept outline so the study companion is useful before any material is
uploaded. Every summary here is original wording written for this project (no exam-objective text, no question
bank). Each concept is one searchable passage; the term is the concept that mastery is tracked against."""

ID = "it-fundamentals"
TITLE = "IT Fundamentals"

OUTLINE = [
    ("Hardware", [
        ("CPU", "The CPU is the chip that carries out program instructions. It fetches an instruction, decodes it and executes it, billions of times a second, and its cores let several tasks run at the same moment."),
        ("RAM", "RAM is the fast working memory a computer uses for programs that are running right now. It is volatile, so everything in it is lost when the power goes off."),
        ("Storage drive", "A storage drive keeps data when the power is off. A solid-state drive stores it in flash cells and is fast and silent; a hard disk drive stores it on spinning platters and is cheaper per gigabyte but slower."),
        ("Motherboard", "The motherboard is the main circuit board that connects the CPU, memory, storage and expansion cards and carries power and data between them."),
        ("Power supply", "The power supply converts mains alternating current into the low direct-current voltages the components need. One that is too weak for the parts causes random shutdowns and restarts."),
        ("GPU", "A GPU is a processor built for doing many simple calculations in parallel. It draws the picture on the screen and also speeds up work such as video encoding and machine learning."),
        ("Firmware", "Firmware such as UEFI is the small program stored on the motherboard that starts the hardware, checks it, and hands control to the operating system's boot loader."),
        ("Peripheral", "A peripheral is a device attached to the computer from outside, such as a keyboard, printer or webcam. It needs a port to connect through and a driver so the operating system can talk to it."),
    ]),
    ("Operating Systems", [
        ("Kernel", "The kernel is the core of the operating system. It decides which program gets the CPU, hands out memory, and is the only part allowed to talk to hardware directly."),
        ("Process", "A process is a running program together with its own private memory. The operating system switches between processes so quickly that they appear to run at once."),
        ("File system", "A file system is the scheme a drive uses to organise data into files and folders and to remember where each piece is stored. NTFS, ext4 and APFS are examples."),
        ("Device driver", "A device driver is software that translates the operating system's general requests into the specific commands one piece of hardware understands."),
        ("Virtual memory", "Virtual memory lets programs use more memory than is physically installed by moving pages that are not in use to a file on the drive. It keeps the system working but is far slower than real RAM."),
        ("User permissions", "User permissions are rules that say which accounts may read, change or run each file and setting. A standard account limits the damage a mistake or malware can do."),
        ("Command line", "The command line is a text interface where you type commands instead of clicking. It is precise, can be scripted, and works over a remote connection."),
        ("Service", "A service, called a daemon on Linux, is a program the operating system keeps running in the background without a window, such as a print spooler or a web server."),
    ]),
    ("Networking", [
        ("IP address", "An IP address is the number that identifies a device on a network so data can be delivered to it. IPv4 addresses are 32 bits long and IPv6 addresses are 128 bits long."),
        ("Subnet mask", "A subnet mask marks which part of an IP address names the network and which part names the host. Two devices with the same network part can talk directly without a router."),
        ("DNS", "DNS is the system that turns a name such as example.com into the IP address computers actually connect to. When it fails, sites stop loading by name even though the connection itself is fine."),
        ("DHCP", "DHCP hands out an IP address, subnet mask, gateway and DNS server to a device automatically when it joins a network, so nobody has to type them in."),
        ("Switch", "A switch connects devices inside one local network. It learns which MAC address is on which port and forwards each frame only to the port that needs it."),
        ("Router", "A router joins different networks together and forwards packets between them using IP addresses. A home router links the local network to the internet provider."),
        ("TCP and UDP", "TCP sets up a connection with a three-way handshake and resends anything lost, so data arrives complete and in order. UDP just sends, with no guarantee, which makes it faster for calls, games and streaming."),
        ("Port number", "A port number identifies which program on a device a piece of traffic is for. Web servers listen on 80 and 443, and remote shells on 22."),
        ("Default gateway", "The default gateway is the router address a device sends traffic to when the destination is outside its own network."),
        ("NAT", "NAT lets many devices with private addresses share one public IP address. The router rewrites the addresses on the way out and remembers which reply belongs to which device."),
    ]),
    ("Security", [
        ("CIA triad", "The CIA triad names the three goals of security: confidentiality keeps data secret, integrity keeps it correct and unaltered, and availability keeps it reachable when needed."),
        ("Authentication", "Authentication proves who someone is, with something they know, have or are. Authorization comes after it and decides what that person is allowed to do."),
        ("Multi-factor authentication", "Multi-factor authentication asks for two or more different kinds of proof, such as a password and a code from a phone, so a stolen password alone is not enough."),
        ("Encryption", "Encryption scrambles data with a key so only someone holding the right key can read it. Symmetric encryption uses one shared key; asymmetric encryption uses a public and private pair."),
        ("Firewall", "A firewall filters network traffic against a set of rules, allowing what is expected and blocking the rest, at the edge of a network or on a single computer."),
        ("Malware", "Malware is software written to cause harm. Ransomware locks files for payment, a trojan hides inside something that looks useful, and a worm spreads by itself across a network."),
        ("Phishing", "Phishing is a message that pretends to come from someone trusted in order to trick a person into giving up a password or opening a harmful file."),
        ("Least privilege", "Least privilege means giving each account and program only the access it needs for its job, so a compromise reaches as little as possible."),
        ("Patching", "Patching is installing updates that fix known flaws. Most successful attacks use weaknesses for which a fix already existed."),
    ]),
    ("Cloud", [
        ("Cloud computing", "Cloud computing is renting computing resources over the internet and paying for what you use, instead of buying and running your own hardware."),
        ("IaaS", "Infrastructure as a service rents the raw building blocks: virtual machines, storage and networks. You manage the operating system and everything above it."),
        ("PaaS", "Platform as a service gives you a ready environment to run your code. The provider looks after the servers, operating system and runtime."),
        ("SaaS", "Software as a service is a finished application used through a browser, such as web mail. The provider runs all of it and you only use it."),
        ("Virtual machine", "A virtual machine is a complete computer simulated in software. A hypervisor shares one physical machine's CPU, memory and storage between several of them."),
        ("Container", "A container packages an application with its libraries and runs it isolated from others while sharing the host's kernel, so it starts faster and is lighter than a virtual machine."),
        ("Shared responsibility", "Under shared responsibility the cloud provider secures the infrastructure it runs, and the customer secures what they put on it: their data, accounts and settings."),
        ("Elasticity", "Elasticity is the ability to add resources automatically when demand rises and release them when it falls, so capacity follows the real load."),
    ]),
    ("Databases", [
        ("Relational database", "A relational database stores data in tables of rows and columns and links the tables through keys, which avoids keeping the same fact in two places."),
        ("Primary key", "A primary key is the column, or set of columns, whose value identifies exactly one row in a table. It can never repeat and never be empty."),
        ("Foreign key", "A foreign key is a column that holds the primary key of a row in another table. It is how a database records that two rows are related."),
        ("SQL", "SQL is the language used to ask a relational database for data and to change it. SELECT reads rows, INSERT adds them, UPDATE changes them and DELETE removes them."),
        ("Index", "An index is a sorted lookup structure on a column that lets the database find matching rows without scanning the whole table. It speeds up reads and slows writes a little."),
        ("Transaction", "A transaction is a group of changes that succeed together or not at all, so the data is never left half-updated, as in a transfer between two accounts."),
        ("Normalization", "Normalization is organising tables so each fact is stored once. It prevents the contradictions that appear when copies of the same data drift apart."),
        ("Backup", "A backup is a separate copy of data kept so it can be restored after loss. It only counts once a restore from it has been tested."),
    ]),
    ("Troubleshooting", [
        ("Troubleshooting method", "A troubleshooting method is a fixed order of work: identify the problem, form a theory of the cause, test it, plan and apply the fix, confirm it works, and write down what was done."),
        ("Ping", "Ping sends a small test message to another device and reports whether a reply came back and how long it took. It answers the first question: can I reach it at all?"),
        ("Traceroute", "Traceroute lists each router on the path to a destination and the delay at each one, which shows where along the way traffic stops or slows down."),
        ("Event log", "An event log is the operating system's record of errors, warnings and notable actions with their times. It often names the failing component directly."),
        ("Safe mode", "Safe mode starts the operating system with only essential drivers and services. If the problem disappears there, something that loads normally is the cause."),
        ("Isolating the cause", "Isolating the cause means changing one thing at a time and testing after each change, so you know exactly which change made the difference."),
        ("Layered approach", "A layered approach checks a network fault from the bottom up: cable and link first, then the address, then the route, then name resolution, then the application."),
        ("Rollback", "A rollback returns a system to the last state that worked, by removing an update, restoring a driver or reverting a setting, when a change made things worse."),
    ]),
]
