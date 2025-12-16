from tkinter import *
import tkinter.messagebox
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket
import threading
import sys
import traceback
import os
import io
from random import randint
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # Initiation..
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0
        self.is_hd = False  # Giả định mặc định là SD
        self.SETUP_HD = 4  # Định nghĩa hằng số cho SETUP HD (nếu có)
        self.is_server_sending = False  # Biến trạng thái để kiểm soát Fake Pause/Resume

    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4,
                        sticky=W+E+N+S, padx=5, pady=5)

    # Xử lí khi nhấn nút Setup
    def setupMovie(self):
        """
        SETUP chỉ được thực hiện khi client đang ở trạng thái INIT
        (chưa tạo phiên RTSP với server).
        """

        # Chỉ cho phép SETUP khi đang ở trạng thái INIT
        if self.state != self.INIT:
            return

        # Kiểm tra chế độ chất lượng video
        # - is_hd = True  -> SETUP video HD
        # - is_hd = False -> SETUP video SD
        if self.is_hd:
            # Gửi yêu cầu SETUP_HD lên server
            # Mục đích: yêu cầu server chuẩn bị stream video HD
            self.sendRtspRequest(self.SETUP_HD)
        else:
            # Gửi yêu cầu SETUP thường (SD)
            # Mục đích: yêu cầu server chuẩn bị stream video chất lượng thường
            self.sendRtspRequest(self.SETUP)

    #Xử lí khi nhấn nút Teardown / thoát chương trình
    def exitClient(self):
        """
        - Gửi TEARDOWN lên server
        - Đóng giao diện Client
        """
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()

        
    # Xử lí khi nhấn nút Pause
    def pauseMovie(self):
        # Chỉ xử lý khi đang ở trạng thái PLAYING
        if self.state == self.PLAYING:

            # Fake Pause: KHÔNG gửi lệnh PAUSE lên server
            # Lý do: Server vẫn tiếp tục gửi RTP, nhưng client tạm thời không hiển thị
            # Điều này giúp Play lại cực nhanh, không cần thiết lập lại luồng RTP

            # Chặn thread nhận RTP bằng cách kích hoạt playEvent
            # (thread listenRtp thấy playEvent.set() sẽ tạm dừng xử lý frame)
            if hasattr(self, "playEvent"):
                self.playEvent.set()

            # Tắt vòng lặp UI để dừng hiển thị khung hình
            if hasattr(self, "ui_loop_running"):
                self.ui_loop_running = False

            # Chuyển trạng thái về READY
            # READY trong trường hợp này = “đang Fake Pause”
            self.state = self.READY
            
    # Xử lí khi nhấn nút Play
    def playMovie(self):
        # Chỉ xử lý khi đang ở trạng thái READY
        # Trạng thái READY có thể đến từ 2 trường hợp:
        # 1. Vừa SETUP xong → server chưa gửi RTP
        # 2. Vừa PAUSE giả → server vẫn đang gửi RTP nhưng client tạm dừng hiển thị

        if self.state == self.READY:

            # [Trường hợp 1: PLAY lần đầu tiên]
            # Server chưa gửi RTP nên ta phải gửi PLAY thật
            if not self.is_server_sending:

                # Tạo thread nhận gói RTP từ server
                threading.Thread(target=self.listenRtp, daemon=True).start()

                # Cho phép thread nhận RTP hoạt động
                # (playEvent.clear() nghĩa là không chặn thread)
                self.playEvent = threading.Event()
                self.playEvent.clear()

                # Gửi yêu cầu PLAY thật lên server để bắt đầu truyền RTP
                self.sendRtspRequest(self.PLAY)

                # Đánh dấu server đã bắt đầu gửi RTP
                self.is_server_sending = True

            else:
                # [Trường hợp 2: Resume sau Fake Pause]
                # Trong Fake Pause, server vẫn gửi RTP đều đặn
                # Ta KHÔNG gửi PLAY lần 2 (vì server đã phát rồi)
                # Client chỉ cần mở lại UI và tiếp tục hiển thị khung hình
                pass

        # Chuyển sang trạng thái PLAYING cho cả hai trường hợp:
        # 1. Lần đầu PLAY
        # 2. Resume sau Fake Pause
        self.state = self.PLAYING

        # Khởi động vòng lặp UI nếu chưa chạy
        # Vòng lặp UI chỉ được chạy đúng 1 lần để tránh xung đột
        if not hasattr(self, 'ui_loop_running') or not self.ui_loop_running:
            self.ui_loop_running = True
            self.run_ui_loop()

    def listenRtp(self):
        """Listen for RTP packets."""
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    currFrameNbr = rtpPacket.seqNum()
                    print("Current Seq Num: " + str(currFrameNbr))

                    if currFrameNbr > self.frameNbr:  # Discard the late packet
                        self.frameNbr = currFrameNbr
                        self.updateMovie(self.writeFrame(
                            rtpPacket.getPayload()))
            except:
                # Stop listening upon requesting PAUSE or TEARDOWN
                if self.playEvent.isSet():
                    break

                # Upon receiving ACK for TEARDOWN request,
                # close the RTP socket
                if self.teardownAcked == 1:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break

    def writeFrame(self, data):
        """Write the received frame to a temp image file. Return the image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        file = open(cachename, "wb")
        file.write(data)
        file.close()

        return cachename

    def updateMovie(self, imageFile):
        """Update the image file as video frame in the GUI."""
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning(
                'Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""

        # Setup request
        request = ""  # Khởi tạo biến request

        # Khối xử lý SETUP (Bao gồm cả SETUP thường và SETUP_HD)
        if (requestCode == self.SETUP or requestCode == self.SETUP_HD) and self.state == self.INIT:
            # Khởi động luồng nhận phản hồi từ Server
            threading.Thread(target=self.recvRtspReply).start()

            # 1. Update RTSP sequence number.
            self.rtspSeq += 1

            # 2. Write the RTSP request to be sent.
            request = "SETUP " + self.fileName + " RTSP/1.0\n"
            request += "CSeq: " + str(self.rtspSeq) + "\n"
            request += "Transport: RTP/UDP; client_port=" + str(self.rtpPort)

            # LOGIC TÙY CHỈNH CHO HD
            if requestCode == self.SETUP_HD:
                request += "\nQuality: HD"  # Thêm header xuống dòng

            # 3. Keep track of the sent request.
            self.requestSent = requestCode  # <-- SỬA LỖI: Lưu mã lệnh thực tế (SETUP hoặc SETUP_HD)

        # Play request
        elif requestCode == self.PLAY and self.state == self.READY:
            # 1. Update RTSP sequence number.
            self.rtspSeq += 1

            # 2. Write the RTSP request to be sent.
            # Yêu cầu PLAY cần Session ID đã nhận được từ SETUP
            request = "PLAY " + self.fileName + " RTSP/1.0\n"
            request += "CSeq: " + str(self.rtspSeq) + "\n"
            request += "Session: " + str(self.sessionId)

            # 3. Keep track of the sent request.
            self.requestSent = self.PLAY

        # Pause request (Lưu ý: Logic pauseMovie() trong code của bạn dùng "Fake Pause"
        # nên request này chỉ được gửi khi bạn thay đổi logic pauseMovie)
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            # 1. Update RTSP sequence number.
            self.rtspSeq += 1

            # 2. Write the RTSP request to be sent.
            request = "PAUSE " + self.fileName + " RTSP/1.0\n"
            request += "CSeq: " + str(self.rtspSeq) + "\n"
            request += "Session: " + str(self.sessionId)

            # 3. Keep track of the sent request.
            self.requestSent = self.PAUSE

        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            # 1. Update RTSP sequence number.
            self.rtspSeq += 1

            # 2. Write the RTSP request to be sent.
            request = "TEARDOWN " + self.fileName + " RTSP/1.0\n"
            request += "CSeq: " + str(self.rtspSeq) + "\n"
            request += "Session: " + str(self.sessionId)

            # 3. Keep track of the sent request.
            self.requestSent = self.TEARDOWN
        else:
            return

        # Send the RTSP request using rtspSocket.
        try:
            self.rtspSocket.send(request.encode("utf-8"))
            print('\nData sent:\n' + request)
        except:
            tkMessageBox.showwarning('Send Error', 'Could not send RTSP request.')

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)

            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            # Close the RTSP socket upon requesting Teardown
            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.shutdown(socket.SHUT_RDWR)
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        # Process only if the server reply's sequence number is the same as the request's
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            # New RTSP session ID
            if self.sessionId == 0:
                self.sessionId = session

            # Process only if the session ID is the same
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200:
                    if self.requestSent == self.SETUP or self.requestSent == self.SETUP_HD:
                        # Update RTSP state.
                        self.state = self.READY
                        # Open RTP port.
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING  # Chuyển trạng thái sang PLAYING sau PLAY thành công
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY  # Chuyển trạng thái sang READY sau PAUSE thành công

                        # The play thread exits. A new thread is created on resume.
                        self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT  # Chuyển trạng thái về INIT sau TEARDOWN

                        # Flag the teardownAcked to close the socket.
                        self.teardownAcked = 1

    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""

        # 1. Create a new datagram socket (UDP) to receive RTP packets from the server
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # SOCK_DGRAM = UDP

        # 2. Set the timeout value of the socket to 0.5sec
        self.rtpSocket.settimeout(0.5)

        try:
            # 3. Bind the socket to the address ('', lắng nghe mọi interface)
            # using the RTP port given by the client user
            self.rtpSocket.bind(('', self.rtpPort))
        except:
            tkMessageBox.showwarning(
                'Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:  # When the user presses cancel, resume playing.
            self.playMovie()
